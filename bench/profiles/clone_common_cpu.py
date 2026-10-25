"""Checked public-API CPU comparisons for common structural clone workloads.

The saved baseline should contain ``structural.py`` and matching native modules.
A ``manifest.json`` can pin its native entrypoint, including ``clone_checked``.
Run with ``--compare-dir`` and ``--output``; ``--gc-enabled`` includes collector
cost. No direct native calls are timed as clone performance.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import gc
import hashlib
import json
from pathlib import Path
import platform
import sys
from typing import Any

from bench.profiles import clone_cpu as base


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
    return cases


def _atomic_identity(actual: Any, source: Any) -> bool:
    """The structural copier must retain exact builtin atom identities."""
    kind = type(source)
    if kind is dict:
        return all(
            _atomic_identity(key, old_key) and _atomic_identity(value, old)
            for (key, value), (old_key, old) in zip(
                actual.items(), source.items(), strict=True
            )
        )
    if kind in (list, tuple):
        return all(
            _atomic_identity(value, old)
            for value, old in zip(actual, source, strict=True)
        )
    if dataclasses.is_dataclass(source) and not isinstance(source, type):
        return all(
            _atomic_identity(
                actual[field.name]
                if type(actual) is dict
                else getattr(actual, field.name),
                getattr(source, field.name),
            )
            for field in dataclasses.fields(source)
        )
    if kind in (type(None), bool, int, float, complex, bytes, str):
        return actual is source
    return True


def _check(case: base.Workload, label: str, result: Any) -> bool:
    """Validate public results outside the timed batches."""
    return base._check_result(case, label, result) and (
        label == "stdlib" or _atomic_identity(result, case.source)
    )


def _node_counts(
    source: Any, *, plain: bool = False, preserve_aliases: bool = True
) -> dict[str, int]:
    """Count structural output nodes with each operation's alias semantics."""
    counts = {"lists": 0, "dicts": 0, "tuples": 0, "records": 0}
    seen: set[int] = set()

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

    visit(source)
    return counts


def main() -> None:
    """Compare public Python entrypoints with prepared, checked common inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-dir", type=Path, required=True)
    parser.add_argument("--original-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--gc-enabled", action="store_true")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    from unstd.clone import structural as current

    modules = {
        "current": current,
        "before": base._snapshot(args.compare_dir.resolve(), "common_before"),
    }
    if args.original_dir:
        modules["original"] = base._snapshot(
            args.original_dir.resolve(), "common_original"
        )
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timing_helper_sha256": hashlib.sha256(
            Path(base.__file__).read_bytes()
        ).hexdigest(),
        "gc_enabled_during_batches": args.gc_enabled,
        "gc_threshold": gc.get_threshold(),
        "rounds": args.rounds,
        "timed_surface": "public Python clone.deep/clone.asdict",
        "includes_output_destruction": True,
        "node_count_scope": "known structural output nodes; foreign protocol results excluded",
        "implementations": {
            label: base._metadata(module) for label, module in modules.items()
        },
        "workloads": {},
    }
    for case in _workloads():
        calls = base._callables(case, modules)
        for label, call in calls.items():
            if not _check(case, label, call()):
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
                    preserve_aliases=label in ("current", "python", "stdlib"),
                )
                for label in calls
            },
            "timings": timings,
            "profiles": {label: base._profile(call) for label, call in calls.items()},
        }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
