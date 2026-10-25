"""Output-checked JSONX throughput against saved sources and stdlib controls.

Save the original ``src/unstd`` tree, including its built native extension,
then run this unchanged driver against both trees with the same interpreter::

    python -m bench.profiles.jsonx_throughput \
        --compare-source /tmp/before/src --source src \
        --output /tmp/jsonx-throughput.json

Each repetition launches a fresh worker retaining both installed revisions.
Before/after batches run adjacently on one thread in alternating order.
Payload construction and output validation remain outside the timed calls.
Every operation allocates its full result; no result is cached between calls.

Installed wheel roots are also accepted. For a deployment comparison, add
``--require-native --max-slowdown 1.15 --include records-1k``. Native mode
rejects missing accelerators and inactive CPython 3.14 dispatch. The optional
slowdown limit compares wall-clock latency and saves the report before failing.
It uses the median of the adjacent after/before wall-clock batch ratios;
reports also retain independent latency medians and every original pair.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from functools import partial
import gc
from hashlib import sha256
from importlib.metadata import version
import io
import json
import math
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
import sysconfig
import time
from typing import TYPE_CHECKING, Any

from bench.core import timing
from bench.core.timing import measure
from bench.profiles.billy_serde import _retained


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@dataclass(frozen=True)
class Workload:
    """A public operation and an independent stdlib reference."""

    name: str
    call: Callable[[], Any]
    reference: Callable[[], Any]
    input_bytes: int


def _same(actual: Any, expected: Any) -> bool:
    """Retain types, float bits and object key order when checking a decode."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is float:
        return bool(actual.hex() == expected.hex())
    if type(expected) is dict:
        return list(actual) == list(expected) and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _workloads() -> list[Workload]:
    from unstd.serde import jsonx

    small = {"id": 7, "name": "small", "ok": True, "values": [1, 2]}
    records = [
        {"id": i, "text": 'café, : \\"', "score": i / 7, "active": i % 2 == 0}
        for i in range(1_000)
    ]
    payloads = {
        "small": small,
        "ascii-records-1k": [
            {"id": i, "text": 'ascii, : \\"', "active": i % 2 == 0}
            for i in range(1_000)
        ],
        "unicode-records-1k": [
            {"id": i, "text": 'café 汉字 🙂, : \\"', "active": i % 2 == 0}
            for i in range(1_000)
        ],
        "integers-10k": list(range(10_000)),
        "integers-100k": list(range(100_000)),
        "wide-floats": {str(i): i * 1.5 for i in range(10_000)},
        "floats-10k": [i / 7 for i in range(10_000)],
        "records-1k": records,
        "text-1m": {"text": 'abcdef, : \\" ' * 80_000},
        "unicode-1m": {"text": 'café 汉字 🙂, : \\" ' * 40_000},
    }
    workloads: list[Workload] = []
    for name, value in payloads.items():
        default = json.dumps(value)
        compact = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        workloads.extend(
            (
                Workload(
                    "dumps/" + name,
                    partial(jsonx.dumps, value),
                    partial(json.dumps, value),
                    len(default.encode()),
                ),
                Workload(
                    "dumpb/" + name,
                    partial(jsonx.dumpb, value),
                    partial(_compact, value),
                    len(compact.encode()),
                ),
            )
        )
        # Explicit escaped and raw Unicode documents exercise distinct inputs.
        for representation, document in (
            ("str", default),
            ("bytes", compact.encode()),
        ):
            workloads.append(
                Workload(
                    f"loads/{name}/{representation}",
                    partial(jsonx.loads, document),
                    partial(json.loads, document),
                    len(document.encode() if type(document) is str else document),
                )
            )
    for name, options in (
        ("indent2/records-1k", {"indent": 2}),
        ("indent4/records-1k", {"indent": 4}),
        ("sorted/records-1k", {"sort_keys": True}),
        ("compact/records-1k", {"separators": (",", ":"), "ensure_ascii": False}),
    ):
        reference = partial(json.dumps, records, **options)
        workloads.append(
            Workload(
                "dumps/" + name,
                partial(jsonx.dumps, records, **options),
                reference,
                len(reference().encode()),
            )
        )
    for name in ("ascii-records-1k", "unicode-records-1k", "integers-100k"):
        value = payloads[name]
        reference = partial(json.dumps, value, indent=2)
        workloads.append(
            Workload(
                "dumps/indent2/" + name,
                partial(jsonx.dumps, value, indent=2),
                reference,
                len(reference().encode()),
            )
        )
    for name in ("canonical", "canonical_strict"):
        reference_bytes = partial(_canonical, records)
        workloads.append(
            Workload(
                name + "/records-1k",
                partial(getattr(jsonx, name), records),
                reference_bytes,
                len(reference_bytes()),
            )
        )
    workloads.append(
        Workload(
            "dump/records-1k",
            partial(_dump, jsonx.dump, records),
            partial(_dump, json.dump, records),
            len(json.dumps(records).encode()),
        )
    )
    # These additional cases label structural sharing explicitly. The equal-
    # valued independent matrix is a control; neither replaces existing rows.
    # Every timed call still allocates the complete serialized output.
    for label, shared_row in (
        ("floats-256x256", [1.5] * 256),
        ("varied-floats-256x128", [index / 7 for index in range(128)]),
    ):
        for name, matrix in (
            (f"shared-{label}", [shared_row] * 256),
            (f"independent-{label}", [list(shared_row) for _ in range(256)]),
        ):
            for operation, function, reference_function in (
                ("dumps", jsonx.dumps, json.dumps),
                ("dumpb", jsonx.dumpb, _compact),
            ):
                expected = reference_function(matrix)
                workloads.append(
                    Workload(
                        f"{operation}/{name}",
                        partial(function, matrix),
                        partial(reference_function, matrix),
                        len(expected.encode() if type(expected) is str else expected),
                    )
                )
    return workloads


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _dump(function: Callable[..., Any], value: object) -> str:
    stream = io.StringIO()
    function(value, stream)
    return stream.getvalue()


def _native_checks(jsonx: Any) -> dict[str, Any]:
    """Prove the installed kernels and their public bindings before timing."""
    native = getattr(jsonx, "_native", None)
    if native is None or not jsonx._HAVE_ORJSON or not jsonx._HAVE_MSGSPEC:
        message = "native qualification requires _json_native, orjson and msgspec"
        raise RuntimeError(message)
    for owner, attribute, method in (
        (jsonx, "_native_dumpb", "dumpb"),
        (jsonx, "_native_line", "line"),
    ):
        if not hasattr(native, method) or getattr(
            owner, attribute, None
        ) is not getattr(native, method):
            message = f"inactive public native binding: {attribute}"
            raise RuntimeError(message)
    # Warm public entry points before checking instrumentation-sensitive guards.
    jsonx.loads("null")
    jsonx.dumpb(None)
    dispatch = {
        name: {
            "bound": bool(native.is_bound(getattr(jsonx, name))),
            "ready": bool(native.dispatch_ready(getattr(jsonx, name))),
        }
        for name in ("loads", "dumpb")
    }
    if sys.version_info[:2] == (3, 14) and any(
        not state["bound"] or not state["ready"] for state in dispatch.values()
    ):
        message = f"CPython 3.14 native dispatch is inactive: {dispatch}"
        raise RuntimeError(message)
    records = [
        {"id": index, "text": "café 汉字 🙂", "active": True} for index in range(40)
    ]
    integers = {"items": list(range(40))}
    document = json.dumps(records)
    compact = _compact(records)
    lines = b"\n".join(_compact(row) for row in records) + b"\n"
    probes = {
        "native_integer_encoding": (native.encode(integers), _compact(integers)),
        "native_compact_bytes": (native.dumpb(records, False, None, False), compact),
        "native_ndjson_encoding": (
            native.line(records[0]),
            _compact(records[0]) + b"\n",
        ),
        "native_ndjson_decode": (native.loads_lines(lines), records),
        "public_text": (jsonx.dumps(records), document),
        "public_compact_bytes": (jsonx.dumpb(records), compact),
        "public_decode": (jsonx.loads(document), records),
    }
    for name, (actual, expected) in probes.items():
        if actual is NotImplemented or not _same(actual, expected):
            message = f"native qualification probe failed: {name}"
            raise RuntimeError(message)
    return {"dispatch": dispatch, "probes": dict.fromkeys(probes, True)}


def _provenance(
    source: Path, jsonx: Any, *, require_native: bool = False
) -> dict[str, Any]:
    imported = Path(jsonx.__file__).resolve()
    if imported != source / "unstd/serde/jsonx.py":
        message = f"requested {source}, imported {imported}"
        raise RuntimeError(message)
    files = {
        str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(imported.parent.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
    }
    native_source = source.parent / "native-serde"
    if not native_source.is_dir():
        native_source = source.parent / "native/serde"
    native_files = {
        str(path.relative_to(native_source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(native_source.rglob("*"))
        if path.is_file() and path.suffix in {".c", ".h"}
    }
    native = getattr(jsonx, "_native", None)
    binary = Path(native.__file__).resolve() if native is not None else None
    if require_native and (
        binary is None
        or binary.parent != imported.parent
        or binary.name != f"_json_native{sysconfig.get_config_var('EXT_SUFFIX')}"
    ):
        message = f"requested native extension in {imported.parent}, imported {binary}"
        raise RuntimeError(message)
    qualification = _native_checks(jsonx) if require_native else None
    dispatch = {}
    for name in ("loads", "dumpb"):
        function = getattr(jsonx, name)
        if native is not None and hasattr(native, "dispatch_ready"):
            dispatch[name] = {
                "bound": native.is_bound(function),
                "ready": native.dispatch_ready(function),
            }
    return {
        "source": str(source),
        "imported_jsonx": str(imported),
        "native_module": getattr(native, "__file__", None),
        "dispatch": dispatch,
        "sha256": files,
        "native_source_sha256": native_files,
        "native_qualification": qualification,
    }


def _environment() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "backends": {name: version(name) for name in ("orjson", "msgspec")},
        "compiler_configuration": {
            name: sysconfig.get_config_var(name)
            for name in ("CC", "CFLAGS", "PY_CFLAGS", "CONFIG_ARGS", "SOABI")
        },
        "cpu_affinity": sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else None,
    }


def _retained_workloads(
    source: Path, include: str, *, require_native: bool
) -> tuple[Any, dict[str, Workload], dict[str, Any]]:
    def load(_benchmark: Path) -> tuple[Any, list[Workload], dict[str, Any], None]:
        from unstd.serde import jsonx

        provenance = _provenance(source, jsonx, require_native=require_native)
        cases = [case for case in _workloads() if not include or include in case.name]
        return (
            jsonx,
            cases,
            {"package_source": jsonx.__file__, "proof": provenance},
            None,
        )

    jsonx, cases, metadata, _ = _retained(Path(__file__), source, loader=load)
    if not cases:
        message = f"no workloads match --include {include!r}"
        raise ValueError(message)
    return jsonx, {case.name: case for case in cases}, metadata["proof"]


def _paired_measure(
    cases: dict[str, Workload], rounds: int, repetition: int
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Keep before/after batches adjacent on one thread, with equal iterations."""
    labels = ("before", "after")
    samples: dict[str, dict[str, list[float]]] = {
        label: {"candidate": [], "stdlib": []} for label in labels
    }
    cpu_samples: dict[str, dict[str, list[float]]] = {
        label: {"candidate": [], "stdlib": []} for label in labels
    }
    calls = {
        label: {"candidate": cases[label].call, "stdlib": cases[label].reference}
        for label in labels
    }
    calibrations: dict[str, dict[str, tuple[int, int]]] = {
        label: {} for label in labels
    }
    iterations = {}
    enabled = gc.isenabled()
    gc.disable()
    try:
        for metric in ("candidate", "stdlib"):
            for label in labels:
                calls[label][metric]()
                calibrations[label][metric] = timing._calibrate(calls[label][metric])
            requested = max(calibrations[label][metric][0] for label in labels)
            slowest = max(
                elapsed / count
                for count, elapsed in (calibrations[label][metric] for label in labels)
            )
            # A 100x faster codec needs many more calibration iterations. Using
            # that count for both can turn the old codec's batch into seconds.
            # Keep equal counts while bounding the estimated slower batch.
            ceiling = max(1, int(timing._MAX_BATCH_NS / max(slowest, 1)))
            iterations[metric] = min(requested, ceiling)
        for index in range(rounds):
            order = labels if (index + repetition) % 2 == 0 else labels[::-1]
            for metric in ("candidate", "stdlib"):
                for label in order:
                    count = iterations[metric]
                    start = time.thread_time_ns()
                    elapsed = timing._batch(calls[label][metric], count)
                    cpu = time.thread_time_ns() - start
                    samples[label][metric].append(elapsed / count)
                    cpu_samples[label][metric].append(cpu / count)
    finally:
        if enabled:
            gc.enable()
        gc.collect()
    measurements: dict[str, dict[str, Any]] = {}
    for label in labels:
        measurements[label] = {}
        for metric in ("candidate", "stdlib"):
            values = samples[label][metric]
            best, worst = min(values), max(values)
            measurements[label][metric] = asdict(
                timing.Measurement(
                    per_op_ns=best,
                    iters=iterations[metric],
                    rounds=rounds,
                    spread=(worst - best) / best if best else 0.0,
                    samples_ns=tuple(values),
                    cpu_samples_ns=tuple(cpu_samples[label][metric]),
                )
            )
        measurements[label]["calibrations"] = calibrations[label]
    pairs = [
        {
            "round": index,
            "order": list(labels if (index + repetition) % 2 == 0 else labels[::-1]),
            "before_ns": samples["before"]["candidate"][index],
            "after_ns": samples["after"]["candidate"][index],
            "after_over_before": samples["after"]["candidate"][index]
            / samples["before"]["candidate"][index],
            "before_stdlib_ns": samples["before"]["stdlib"][index],
            "after_stdlib_ns": samples["after"]["stdlib"][index],
        }
        for index in range(rounds)
    ]
    return measurements, pairs


def _paired_worker(
    before: Path,
    after: Path,
    rounds: int,
    include: str,
    repetition: int,
    *,
    require_native: bool = False,
) -> list[dict[str, Any]]:
    sources = {"before": before, "after": after}
    retained: dict[str, tuple[Any, dict[str, Workload], dict[str, Any]]] = {}
    for label, source in sources.items():
        if label == "after" and before == after:
            retained[label] = retained["before"]
        else:
            retained[label] = _retained_workloads(
                source, include, require_native=require_native
            )
    names = set(retained["before"][1])
    if set(retained["after"][1]) != names:
        message = "comparison sources selected different workloads"
        raise RuntimeError(message)
    for label, (jsonx, _cases, proof) in retained.items():
        if _provenance(sources[label], jsonx, require_native=require_native) != proof:
            message = (
                f"{label} source, native binary or dispatch changed during loading"
            )
            raise RuntimeError(message)
    results: dict[str, dict[str, Any]] = {"before": {}, "after": {}}
    for name in retained["before"][1]:
        cases = {label: retained[label][1][name] for label in sources}
        expected = {label: case.reference() for label, case in cases.items()}
        if not _same(expected["before"], expected["after"]):
            message = f"comparison references differ: {name}"
            raise AssertionError(message)
        for label, case in cases.items():
            if not _same(case.call(), expected[label]):
                message = f"incorrect output: {name}/{label}"
                raise AssertionError(message)
        measurements, pairs = _paired_measure(cases, rounds, repetition)
        for label, case in cases.items():
            if not _same(case.call(), expected[label]):
                message = f"incorrect output after timing: {name}/{label}"
                raise AssertionError(message)
            measured = measurements[label]
            candidate = measured["candidate"]["per_op_ns"]
            results[label][name] = {
                "input_bytes": case.input_bytes,
                "identical_output": True,
                **measured,
                "paired_batches": pairs,
                "candidate_mib_s": case.input_bytes / candidate * 1e9 / 2**20,
                "stdlib_over_candidate": measured["stdlib"]["per_op_ns"] / candidate,
            }
    for label, (jsonx, _cases, proof) in retained.items():
        if _provenance(sources[label], jsonx, require_native=require_native) != proof:
            message = f"{label} source, native binary or dispatch changed during timing"
            raise RuntimeError(message)
    environment = _environment()
    return [
        {
            "label": label,
            "repetition": repetition,
            "environment": environment,
            "provenance": retained[label][2],
            "timings": results[label],
        }
        for label in sources
    ]


def _worker(
    source: Path, rounds: int, include: str, *, require_native: bool = False
) -> dict[str, Any]:
    sys.path.insert(0, str(source))
    from unstd.serde import jsonx

    provenance = _provenance(source, jsonx, require_native=require_native)
    results = {}
    for case in _workloads():
        if include and include not in case.name:
            continue
        expected = case.reference()
        if not _same(case.call(), expected):
            message = f"incorrect output: {case.name}"
            raise AssertionError(message)
        candidate = measure(case.call, rounds=rounds)
        control = measure(case.reference, rounds=rounds)
        if not _same(case.call(), expected):
            message = f"incorrect output after timing: {case.name}"
            raise AssertionError(message)
        results[case.name] = {
            "input_bytes": case.input_bytes,
            "identical_output": True,
            "candidate": asdict(candidate),
            "stdlib": asdict(control),
            "candidate_mib_s": case.input_bytes / candidate.per_op_ns * 1e9 / 2**20,
            "stdlib_over_candidate": control.per_op_ns / candidate.per_op_ns,
        }
    if not results:
        message = f"no workloads match --include {include!r}"
        raise ValueError(message)
    if _provenance(source, jsonx, require_native=require_native) != provenance:
        message = "product source, native binary or dispatch changed during timing"
        raise RuntimeError(message)
    return {
        "environment": _environment(),
        "provenance": provenance,
        "timings": results,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Measure unchanged fixtures against isolated source trees."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("src"))
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--include", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--max-slowdown", type=float)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--paired-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-repetition", type=int, default=0, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if args.rounds < 1 or args.repetitions < 1:
        parser.error("rounds and repetitions must be positive")
    if args.max_slowdown is not None and (
        not math.isfinite(args.max_slowdown) or args.max_slowdown < 1
    ):
        parser.error("--max-slowdown must be finite and at least one")
    if args.max_slowdown is not None and args.compare_source is None:
        parser.error("--max-slowdown requires --compare-source")
    if args.max_slowdown is not None and args.output is None:
        parser.error("--max-slowdown requires --output to retain evidence")
    if args.paired_worker:
        if args.compare_source is None:
            parser.error("--paired-worker requires --compare-source")
        print(
            json.dumps(
                _paired_worker(
                    args.compare_source.resolve(),
                    args.source.resolve(),
                    args.rounds,
                    args.include,
                    args.worker_repetition,
                    require_native=args.require_native,
                )
            )
        )
        return
    if args.worker:
        print(
            json.dumps(
                _worker(
                    args.source.resolve(),
                    args.rounds,
                    args.include,
                    require_native=args.require_native,
                )
            )
        )
        return
    sources = [("after", args.source.resolve())]
    if args.compare_source is not None:
        sources.insert(0, ("before", args.compare_source.resolve()))
    runs = []
    for repetition in range(args.repetitions):
        if args.compare_source is not None:
            command = [
                sys.executable,
                "-m",
                "bench.profiles.jsonx_throughput",
                "--paired-worker",
                "--compare-source",
                str(args.compare_source.resolve()),
                "--source",
                str(args.source.resolve()),
                "--rounds",
                str(args.rounds),
                "--include",
                args.include,
                "--worker-repetition",
                str(repetition),
            ]
            if args.require_native:
                command.append("--require-native")
            result = json.loads(
                subprocess.check_output(  # noqa: S603
                    command,
                    text=True,
                    env=dict(
                        os.environ,
                        # Importing bench eagerly imports product workloads.
                        # CI has dependencies installed globally and products
                        # only in the two explicit target directories.
                        PYTHONPATH=os.pathsep.join(
                            (
                                str(args.source.resolve()),
                                str(Path(__file__).resolve().parents[2]),
                            )
                        ),
                    ),
                )
            )
            if (
                not isinstance(result, list)
                or len(result) != 2
                or {row["label"] for row in result} != {"before", "after"}
                or any(row["repetition"] != repetition for row in result)
            ):
                message = "paired worker did not retain both revision results"
                raise RuntimeError(message)
            runs.extend(result)
            print(f"Measured paired repetition {repetition + 1}", file=sys.stderr)
            continue
        for label, source in sources[:: 1 if repetition % 2 == 0 else -1]:
            command = [
                sys.executable,
                "-m",
                "bench.profiles.jsonx_throughput",
                "--worker",
                "--source",
                str(source),
                "--rounds",
                str(args.rounds),
                "--include",
                args.include,
            ]
            if args.require_native:
                command.append("--require-native")
            result = json.loads(
                subprocess.check_output(  # noqa: S603
                    command,
                    text=True,
                    env=dict(
                        os.environ,
                        PYTHONPATH=os.pathsep.join(
                            (str(source), str(Path(__file__).resolve().parents[2]))
                        ),
                    ),
                )
            )
            runs.append({"label": label, "repetition": repetition, **result})
            print(f"Measured {label} repetition {repetition + 1}", file=sys.stderr)
    if any(run["environment"] != runs[0]["environment"] for run in runs):
        message = "comparison workers used different environments"
        raise RuntimeError(message)
    for run in runs:
        prior = next(row for row in runs if row["label"] == run["label"])
        if run["provenance"] != prior["provenance"]:
            message = f"{run['label']} product changed between repetitions"
            raise RuntimeError(message)
    comparisons = {}
    if args.compare_source is not None:
        names = set(runs[0]["timings"])
        if any(set(run["timings"]) != names for run in runs):
            message = "comparison workers measured different workloads"
            raise RuntimeError(message)
        for name in sorted(names):
            timings = {
                label: [run["timings"][name] for run in runs if run["label"] == label]
                for label in ("before", "after")
            }
            paired_timings = {
                label: {
                    run["repetition"]: run["timings"][name]
                    for run in runs
                    if run["label"] == label
                }
                for label in ("before", "after")
            }
            if any(
                set(values) != set(range(args.repetitions))
                for values in paired_timings.values()
            ):
                message = "comparison workers did not retain complete repetition pairs"
                raise RuntimeError(message)
            pairs = [
                {
                    "repetition": repetition,
                    "before_ns": paired_timings["before"][repetition]["candidate"][
                        "per_op_ns"
                    ],
                    "after_ns": paired_timings["after"][repetition]["candidate"][
                        "per_op_ns"
                    ],
                }
                for repetition in range(args.repetitions)
            ]
            for pair in pairs:
                pair["after_over_before"] = pair["after_ns"] / pair["before_ns"]
            batch_pairs: list[dict[str, Any]] = []
            for repetition in range(args.repetitions):
                old = paired_timings["before"][repetition]["paired_batches"]
                new = paired_timings["after"][repetition]["paired_batches"]
                if old != new or len(new) != args.rounds:
                    message = "comparison did not retain every adjacent batch pair"
                    raise RuntimeError(message)
                batch_pairs.extend({"repetition": repetition, **pair} for pair in new)
            before_ns = median(
                row["candidate"]["per_op_ns"] for row in timings["before"]
            )
            after_ns = median(row["candidate"]["per_op_ns"] for row in timings["after"])
            comparisons[name] = {
                "before_ns": before_ns,
                "after_ns": after_ns,
                "before_over_after": before_ns / after_ns,
                "paired_after_over_before": median(
                    pair["after_ns"] / pair["before_ns"] for pair in batch_pairs
                ),
                "pairs": pairs,
                "batch_pairs": batch_pairs,
                "after_mib_s": median(
                    row["candidate_mib_s"] for row in timings["after"]
                ),
                "before_stdlib_ns": median(
                    row["stdlib"]["per_op_ns"] for row in timings["before"]
                ),
                "after_stdlib_ns": median(
                    row["stdlib"]["per_op_ns"] for row in timings["after"]
                ),
            }
    report = {
        "driver_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "estimator": "median of adjacent before/after wall-clock batch mean ratios on one thread; independent latency medians retained",
        "comparisons": comparisons,
        "runs": runs,
        "require_native": args.require_native,
        "max_slowdown": args.max_slowdown,
        "regressions": [
            name
            for name, result in comparisons.items()
            if args.max_slowdown is not None
            and result["paired_after_over_before"] > args.max_slowdown
        ],
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)
    if report["regressions"]:
        message = f"JSONX wall-clock slowdown exceeds {args.max_slowdown}: {report['regressions']}"
        raise RuntimeError(message)


if __name__ == "__main__":
    main()
