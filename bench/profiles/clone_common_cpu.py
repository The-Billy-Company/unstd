"""Checked public-API CPU comparisons for common structural clone workloads.

The saved baseline should contain ``structural.py`` and matching native modules.
A ``manifest.json`` can pin its native entrypoint, including ``clone_checked``.
Run with ``--compare-dir`` and ``--output``; ``--gc-enabled`` includes collector
cost. ``--infer-native-bindings`` admits native imports from an installed
baseline's own wrapper; ``--require-native`` checks public native routes and
``--max-slowdown`` gates both wall and CPU latency. No direct native calls are
timed as clone performance.
"""

from __future__ import annotations

import argparse
import ast
import copy
import dataclasses
import datetime as dt
from fnmatch import fnmatchcase
import gc
import hashlib
import inspect
import json
import math
from pathlib import Path
import platform
import statistics
import sys
from typing import TYPE_CHECKING, Any

from bench.profiles import clone_cpu as base


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_NATIVE_BINDINGS = {
    "_native_clone": "native_entrypoint",
    "_native_graph": "native_graph_entrypoint",
    "_native_records": "native_records_entrypoint",
    "_native_record_graph": "native_record_graph_entrypoint",
    "_native_root_graph": "native_root_graph_entrypoint",
    "_native_walk_graph": "native_walk_graph_entrypoint",
    "_native_dataclass": "native_dataclass_entrypoint",
}


@dataclasses.dataclass(frozen=True)
class FrozenRecord:
    """A frozen record still requires independent copies of mutable fields."""

    name: str
    values: list[int]


@dataclasses.dataclass(slots=True)
class SlottedRecord:
    """A common record shape retained as an explicit protocol fallback control."""

    name: str
    values: list[int]


@dataclasses.dataclass
class ScalarRecord:
    """An ordinary dataclass whose complete instance state is atomic."""

    ident: int
    name: str
    score: float


def _balanced(depth: int, width: int) -> Any:
    """A distinct, alternating dictionary/list tree built outside measurement."""
    if depth == 0:
        return [7, "leaf", True, None]
    return {
        f"child{index}": [_balanced(depth - 1, width), index] for index in range(width)
    }


def _case(name: str, source: Any, operation: str = "deep") -> base.Workload:
    """Create the untimed stdlib reference and strict output/ownership check."""
    expected = (
        dataclasses.asdict(source) if operation == "asdict" else copy.deepcopy(source)
    )

    def check(result: Any, reuse_tuples: bool) -> bool:
        return base._same(result, expected) and base._isolated(
            result, source, reuse_tuples=reuse_tuples
        )

    return base.Workload(name, source, check, operation)


def _workloads() -> list[base.Workload]:
    """Common nested data, record collections, and protocol fallback controls."""
    cases = [
        case
        for case in base._workloads()
        if case.name
        in {
            "small-dict",
            "nested-200",
            "records-500",
            "tuple-records-500",
            "wide-lists-1000",
            "deep-80",
            "large-records-20000",
            "repeated-aliases-500",
            "dataclasses-200",
            "dataclass-records-500",
            "mixed-keys-1000",
            "foreign-last",
            "asdict-records-500",
        }
    ]
    cases.extend(
        (
            _case(
                "nested-small-5",
                {"rows": [{"id": i, "items": [i, "x"]} for i in range(5)]},
            ),
            _case(
                "nested-wide-1000",
                {"rows": [{"id": i, "items": [i, "x"]} for i in range(1000)]},
            ),
            _case("balanced-dict-list", _balanced(4, 3)),
            _case(
                "dictionary-of-records-500",
                {
                    f"record{index}": {
                        "name": "row",
                        "values": [index, index + 1],
                        "metadata": {"active": True, "ratio": 1.5},
                    }
                    for index in range(500)
                },
            ),
            _case("dataclass-small", base.Record("row", [1, 2])),
            _case(
                "dataclasses-copy-hooks-40",
                [base.Record("row", base.Custom()) for _ in range(40)],
            ),
            _case(
                "dataclasses-frozen-200",
                [FrozenRecord("row", [i, i + 1]) for i in range(200)],
            ),
            _case(
                "dataclasses-slots-200",
                [SlottedRecord("row", [i, i + 1]) for i in range(200)],
            ),
            _case(
                "dataclasses-flat-500",
                [ScalarRecord(i, "row", 1.5) for i in range(500)],
            ),
            _case(
                "dataclasses-datetime-200",
                [
                    base.Record(
                        "row",
                        [
                            dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
                            + dt.timedelta(days=i),
                            i,
                        ],
                    )
                    for i in range(200)
                ],
            ),
            _case(
                "dataclass-tree-200",
                base.Record(
                    "root", [base.Record("row", [i, i + 1]) for i in range(200)]
                ),
            ),
            _case(
                "asdict-tree-200",
                base.Record(
                    "root", [base.Record("row", [i, i + 1]) for i in range(200)]
                ),
                "asdict",
            ),
        )
    )
    shared = {"values": [1, 2, 3]}
    cases.extend(
        (
            _case("scalar-list-1000", list(range(1000))),
            _case("scalar-tuple-1000", tuple(range(1000))),
            _case("scalar-list-100000", list(range(100000))),
            _case("repeated-aliases-10000", [shared] * 10000),
            _case("repeated-aliases-100000", [shared] * 100000),
        )
    )
    return cases


def _atomic_identity(
    actual: Any, source: Any, seen: set[tuple[int, int]] | None = None
) -> bool:
    """Public cloning must retain exact builtin atom identities."""
    if seen is None:
        seen = set()
    pair = (id(actual), id(source))
    if pair in seen:
        return True
    seen.add(pair)
    kind = type(source)
    if kind is dict:
        return all(
            _atomic_identity(key, old_key, seen) and _atomic_identity(value, old, seen)
            for (key, value), (old_key, old) in zip(
                actual.items(), source.items(), strict=True
            )
        )
    if kind in (list, tuple):
        return all(
            _atomic_identity(value, old, seen)
            for value, old in zip(actual, source, strict=True)
        )
    if dataclasses.is_dataclass(source) and not isinstance(source, type):
        return all(
            _atomic_identity(
                actual[field.name]
                if type(actual) is dict
                else getattr(actual, field.name),
                getattr(source, field.name),
                seen,
            )
            for field in dataclasses.fields(source)
        )
    model_base = getattr(sys.modules.get("pydantic"), "BaseModel", None)
    if isinstance(model_base, type) and isinstance(source, model_base):
        return all(
            _atomic_identity(getattr(actual, name), getattr(source, name), seen)
            for name in ("__dict__", "__pydantic_extra__", "__pydantic_private__")
        )
    if kind in (type(None), bool, int, float, complex, bytes, str):
        return actual is source
    return True


def _check(
    case: base.Workload,
    label: str,
    result: Any,
    *,
    shared_aliases: bool | None = None,
) -> bool:
    """Validate public results outside the timed batches."""
    return base._check_result(case, label, result, shared_aliases=shared_aliases) and (
        label == "stdlib" or _atomic_identity(result, case.source)
    )


def _node_counts(
    source: Any, *, plain: bool = False, preserve_aliases: bool = True
) -> dict[str, int]:
    """Count structural output nodes with each operation's alias semantics."""
    counts = {"lists": 0, "dicts": 0, "tuples": 0, "records": 0}
    seen: set[int] = set()
    model_base = getattr(sys.modules.get("pydantic"), "BaseModel", None)

    def visit(value: Any) -> None:
        kind = type(value)
        if preserve_aliases and not plain:
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
        if kind is dict:
            counts["dicts"] += 1
            for key, child in value.items():
                visit(key)
                visit(child)
        elif kind is list or kind is tuple:
            counts["lists" if kind is list else "tuples"] += 1
            for child in value:
                visit(child)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            counts["dicts"] += plain or hasattr(value, "__dict__")
            counts["records"] += not plain
            for field in dataclasses.fields(value):
                visit(getattr(value, field.name))
        elif isinstance(model_base, type) and isinstance(value, model_base):
            counts["records"] += 1
            for name in ("__dict__", "__pydantic_extra__", "__pydantic_private__"):
                visit(getattr(value, name))

    visit(source)
    return counts


def _snapshot(directory: Path, label: str, *, infer_native_bindings: bool) -> Any:
    """Load each baseline's native imports from that baseline's own extension."""
    module = base._snapshot(directory, label)
    if not infer_native_bindings:
        return module
    native = module._native_clone
    backend = sys.modules[native.__module__] if native is not None else None
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    imports = {
        imported.asname: imported.name
        for node in ast.walk(ast.parse((directory / "structural.py").read_text()))
        if isinstance(node, ast.ImportFrom)
        and node.module == "unstd.clone._clone_native"
        for imported in node.names
        if imported.asname is not None and imported.asname in _NATIVE_BINDINGS
    }
    for binding, entrypoint in imports.items():
        if _NATIVE_BINDINGS[binding] not in manifest:
            setattr(
                module,
                binding,
                getattr(backend, entrypoint, None) if backend is not None else None,
            )
    if hasattr(module, "_NATIVE_SELECTED_MEMO"):
        module._NATIVE_SELECTED_MEMO = module._native_walk_graph is not None
    if hasattr(module, "_NATIVE_MODEL_CONTINUATION"):
        module._NATIVE_MODEL_CONTINUATION = bool(
            backend is not None and getattr(backend, "MODEL_CONTINUATION", False)
        )
    return module


def _native_routes(
    module: Any,
    label: str,
    *,
    structural_baseline: bool = False,
    require_models: bool = False,
) -> dict[str, list[str]]:
    """Prove public native routes under the revision's actual copy contract."""
    if structural_baseline and base._copy_contract(module) != "structural":
        message = f"{label} does not have the historical structural copy contract"
        raise RuntimeError(message)
    bindings = {
        name: getattr(module, name, None)
        for name in _NATIVE_BINDINGS
        if getattr(module, name, None) is not None
    }
    successful: list[str] = []

    def traced(name: str, call: Callable[..., Any]) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            result = call(*args, **kwargs)
            if result is not NotImplemented and inspect.isbuiltin(call):
                successful.append(name)
            return result

        return invoke

    shared = [1, {"values": [2, 3]}]
    record = base.Record("row", shared)
    probes: tuple[tuple[base.Workload, set[str]], ...] = (
        (
            _case("builtin-graph", {"left": shared, "right": shared}),
            {"_native_graph", "_native_root_graph", "_native_walk_graph"},
        ),
        (
            _case("mixed-dataclass", {"rows": [record, record], "values": shared}),
            {"_native_walk_graph", "_native_record_graph", "_native_dataclass"},
        ),
        (
            _case(
                "asdict-tree",
                base.Record("root", {"left": shared, "right": shared}),
                "asdict",
            ),
            {"_native_clone", "_native_records"},
        ),
    )
    if structural_baseline:
        # Older releases accelerate exact builtin trees and intentionally copy
        # shared branches independently. Their mixed-record and asdict paths
        # remain measured and checked below, but did not have native kernels.
        probes = (
            (
                _case("builtin-tree", {"left": shared, "right": shared}),
                {"_native_clone"},
            ),
        )
    routes: dict[str, list[str]] = {}
    try:
        for name, call in bindings.items():
            if not callable(call):
                message = f"{label} native binding {name} is not callable"
                raise TypeError(message)
            setattr(module, name, traced(name, call))
        for case, accepted in probes:
            successful.clear()
            result = getattr(module, case.operation)(case.source)
            valid = _check(
                case, "current", result, shared_aliases=not structural_baseline
            )
            if case.name == "builtin-tree":
                valid = valid and result["left"] is not result["right"]
            elif case.name == "builtin-graph":
                valid = valid and result["left"] is result["right"]
            elif case.name == "mixed-dataclass":
                valid = (
                    valid
                    and result["rows"][0] is result["rows"][1]
                    and result["rows"][0].values is result["values"]
                )
            else:
                valid = (
                    valid and result["values"]["left"] is not result["values"]["right"]
                )
            if not valid or not accepted.intersection(successful):
                message = (
                    f"native clone route unavailable or incorrect: {label}/{case.name}"
                )
                raise RuntimeError(message)
            routes[case.name] = sorted(set(successful))
        if require_models:
            from bench.workloads import clone_consumers

            model = getattr(module, "_model", None)
            code = getattr(model, "__code__", None)
            if (
                not getattr(module, "_NATIVE_MODEL_CONTINUATION", False)
                or not inspect.iscode(code)
                or model is not getattr(module, "_MODEL_WALK", None)
                or code is not getattr(module, "_MODEL_WALK_CODE", None)
            ):
                message = f"native model continuation unavailable: {label}"
                raise RuntimeError(message)
            case = next(
                case
                for case in clone_consumers.workloads()
                if case.name == "consumer-question-private-alias"
            )
            if module._record_admit(type(case.source))[1] is not model:
                message = f"native model admission does not select its canonical handler: {label}"
                raise RuntimeError(message)
            # Warm the optional Undefined marker before profiling.
            module.deep(case.source)
            python_models = 0

            def observe(frame: Any, event: str, _target: Any) -> None:
                nonlocal python_models
                if event == "call" and frame.f_code is code:
                    python_models += 1

            previous_profile = sys.getprofile()
            successful.clear()
            sys.setprofile(observe)
            try:
                result = module.deep(case.source)
            finally:
                sys.setprofile(previous_profile)
            if (
                not case.check(result, False)
                or python_models
                or "_native_walk_graph" not in successful
            ):
                message = f"native model route unavailable or used Python model reconstruction: {label}"
                raise RuntimeError(message)
            routes["pydantic-model"] = sorted(set(successful))
    finally:
        for name, call in bindings.items():
            setattr(module, name, call)
    return routes


def _matched_slowdown(
    current: dict[str, Any], before: dict[str, Any], metric: str
) -> float:
    """Pair rounds before taking the median so common worker drift cancels."""
    current_samples = current.get("samples")
    before_samples = before.get("samples")
    if current_samples is None and before_samples is None:
        return float(current[metric] / before[metric])
    if (
        not isinstance(current_samples, list)
        or not isinstance(before_samples, list)
        or not current_samples
        or len(current_samples) != len(before_samples)
    ):
        message = "clone comparisons require nonempty matched timing rounds"
        raise ValueError(message)
    sample_metric = metric.removeprefix("median_")
    ratios = []
    for after, prior in zip(current_samples, before_samples, strict=True):
        current_ns, before_ns = after[sample_metric], prior[sample_metric]
        if (
            not math.isfinite(current_ns)
            or not math.isfinite(before_ns)
            or current_ns <= 0
            or before_ns <= 0
        ):
            message = "clone timing samples must be finite and positive"
            raise ValueError(message)
        ratios.append(current_ns / before_ns)
    return float(statistics.median(ratios))


def _regressions(
    workloads: dict[str, Any], max_slowdown: float
) -> list[dict[str, Any]]:
    """Compare matched wall and CPU rounds against the same-worker baseline."""
    failures = []
    for name, workload in workloads.items():
        timings = workload["timings"]
        for metric in ("median_wall_ns", "median_cpu_ns"):
            before = timings["before"][metric]
            current = timings["current"][metric]
            slowdown = _matched_slowdown(timings["current"], timings["before"], metric)
            if not math.isfinite(slowdown) or slowdown > max_slowdown:
                failures.append(
                    {
                        "workload": name,
                        "metric": metric,
                        "before_ns": before,
                        "current_ns": current,
                        "slowdown": slowdown,
                    }
                )
    return failures


def _write_output(output: dict[str, Any], path: Path | None) -> None:
    """Retain measurements before a failed gate exits."""
    rendered = json.dumps(output, indent=2) + "\n"
    if path:
        path.write_text(rendered)
    else:
        print(rendered)


def _target_result(timings: dict[str, Any], target: float) -> dict[str, Any]:
    """Keep the requested stdlib speed target independent of regression limits."""
    cpu_speedup = 1 / _matched_slowdown(
        timings["current"], timings["stdlib"], "median_cpu_ns"
    )
    wall_speedup = 1 / _matched_slowdown(
        timings["current"], timings["stdlib"], "median_wall_ns"
    )
    return {
        "required_speedup": target,
        "matched_cpu_speedup": cpu_speedup,
        "matched_wall_speedup": wall_speedup,
        "cpu_target_ns": timings["stdlib"]["median_cpu_ns"] / target,
        "wall_target_ns": timings["stdlib"]["median_wall_ns"] / target,
        "further_cpu_speedup_needed": target / cpu_speedup,
        "further_wall_speedup_needed": target / wall_speedup,
        "met": min(cpu_speedup, wall_speedup) >= target,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Compare public Python entrypoints with prepared, checked common inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--original-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--gc-enabled", action="store_true")
    parser.add_argument("--infer-native-bindings", action="store_true")
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--max-slowdown", type=float)
    parser.add_argument("--consumer-cases", action="store_true")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--target-speedup", type=float)
    parser.add_argument("--enforce-target", action="store_true")
    args = parser.parse_args(argv)
    if args.rounds < 1:
        parser.error("rounds must be positive")
    if args.max_slowdown is not None and (
        not math.isfinite(args.max_slowdown) or args.max_slowdown < 1
    ):
        parser.error("max-slowdown must be finite and at least 1")
    if args.target_speedup is not None and (
        not math.isfinite(args.target_speedup) or args.target_speedup < 1
    ):
        parser.error("target-speedup must be finite and at least 1")
    if args.enforce_target and args.target_speedup is None:
        parser.error("enforce-target requires target-speedup")
    from unstd.clone import structural as current

    if args.source_dir is not None:
        current = _snapshot(
            args.source_dir.resolve(),
            "common_current",
            infer_native_bindings=args.infer_native_bindings,
        )

    modules = {
        "current": current,
        "before": _snapshot(
            args.compare_dir.resolve(),
            "common_before",
            infer_native_bindings=args.infer_native_bindings,
        ),
    }
    if args.original_dir:
        modules["original"] = _snapshot(
            args.original_dir.resolve(),
            "common_original",
            infer_native_bindings=args.infer_native_bindings,
        )
    contracts = {
        label: base._copy_contract(module) for label, module in modules.items()
    }
    if contracts["current"] != "shared":
        message = "current public deep must preserve shared references"
        raise AssertionError(message)
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timing_helper_sha256": hashlib.sha256(
            Path(base.__file__).read_bytes()
        ).hexdigest(),
        "gc_enabled_during_batches": args.gc_enabled,
        "calibration": {
            "batch_target_ns": base.BATCH_TARGET_NS,
            "probes_per_count": base.CALIBRATION_PROBES,
            "count_selection": "fastest calibration probe",
        },
        "gc_threshold": gc.get_threshold(),
        "rounds": args.rounds,
        "infer_native_bindings": args.infer_native_bindings,
        "require_native": args.require_native,
        "timed_surface": "public Python clone.deep/clone.asdict",
        "copy_contracts": contracts,
        "includes_output_destruction": True,
        "cache_state": "admission and field caches warmed by untimed validation and calibration",
        "node_count_scope": "known structural output nodes; foreign protocol results excluded",
        "implementations": {
            label: base._metadata(module) for label, module in modules.items()
        },
        "workloads": {},
    }
    if args.require_native:
        output["native_routes"] = {}
        try:
            for label in ("current", "before"):
                output["native_routes"][label] = _native_routes(
                    modules[label],
                    label,
                    structural_baseline=(
                        label == "before" and contracts[label] == "structural"
                    ),
                    require_models=args.consumer_cases
                    and bool(
                        getattr(modules[label], "_NATIVE_MODEL_CONTINUATION", False)
                    ),
                )
        except RuntimeError as error:
            output["native_error"] = str(error)
            _write_output(output, args.output)
            raise SystemExit(1) from error
    cases = _workloads()
    if args.consumer_cases:
        if any(contract != "shared" for contract in contracts.values()):
            message = "consumer clone comparisons require shared-reference baselines"
            raise ValueError(message)
        from bench.workloads import clone_consumers

        cases.extend(clone_consumers.workloads())
        output["consumer_workloads_sha256"] = hashlib.sha256(
            Path(clone_consumers.__file__).read_bytes()
        ).hexdigest()
    selected = [
        case
        for case in cases
        if not args.include
        or any(fnmatchcase(case.name, pattern) for pattern in args.include)
    ]
    if not selected:
        parser.error("include patterns selected no clone workloads")
    output["selection_patterns"] = args.include
    output["consumer_cases_enabled"] = args.consumer_cases
    for case in selected:
        calls = base._callables(case, modules)
        for label, call in calls.items():
            if not _check(
                case,
                label,
                call(),
                shared_aliases=contracts.get(label, "shared") == "shared",
            ):
                message = f"incorrect public output: {case.name}/{label}"
                raise AssertionError(message)
        timings = base._timings(calls, args.rounds, args.gc_enabled)
        for values in timings.values():
            for reference in ("stdlib", "before", "original"):
                if reference in timings:
                    values[f"cpu_speedup_vs_{reference}"] = (
                        timings[reference]["median_cpu_ns"] / values["median_cpu_ns"]
                    )
        output["workloads"][case.name] = {
            "operation": case.operation,
            "output_node_counts": _node_counts(
                case.source, plain=case.operation == "asdict"
            ),
            "output_node_counts_by_implementation": {
                label: _node_counts(
                    case.source,
                    plain=case.operation == "asdict",
                    preserve_aliases=contracts.get(label, "shared") == "shared",
                )
                for label in calls
            },
            "timings": timings,
            "profiles": {label: base._profile(call) for label, call in calls.items()},
        }
    failures: list[dict[str, Any]] = []
    if args.max_slowdown is not None:
        try:
            failures = _regressions(output["workloads"], args.max_slowdown)
        except ValueError as error:
            output["timing_error"] = str(error)
            _write_output(output, args.output)
            raise SystemExit(1) from error
        output["performance_gate"] = {
            "max_slowdown": args.max_slowdown,
            "comparison": "median of matched current/before batch ratios",
            "passed": not failures,
            "regressions": failures,
        }
    target_misses: list[str] = []
    if args.target_speedup is not None:
        try:
            for name, row in output["workloads"].items():
                row["stdlib_target"] = _target_result(
                    row["timings"], args.target_speedup
                )
                if not row["stdlib_target"]["met"]:
                    target_misses.append(name)
        except ValueError as error:
            output["target_error"] = str(error)
            _write_output(output, args.output)
            raise SystemExit(1) from error
        output["stdlib_target"] = {
            "required_speedup": args.target_speedup,
            "comparison": "median of matched stdlib/current wall and CPU batch ratios",
            "enforced": args.enforce_target,
            "passed": not target_misses,
            "misses": target_misses,
        }
    _write_output(output, args.output)
    if (args.max_slowdown is not None and failures) or (
        args.enforce_target and target_misses
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
