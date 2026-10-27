"""Compare package sources using existing, output-checked serde fixtures.

Run with the consumer's interpreter so Python and optional backends stay fixed.

The original benchmark owns workloads, references and CPU measurement.
This runner alternates isolated workers or source-bound calls within one process,
checks import provenance, and retains raw reports.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import suppress
from dataclasses import replace
from functools import partial
import gc
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from typing import Any


_ROOT = Path(__file__).resolve().parents[2]
_BENCHMARK = Path("services/ai/tests/tooling/bench/bench_serde.py")


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _source(path: Path) -> dict[str, Any]:
    package = path / "unstd"
    if not package.is_dir():
        message = f"source must contain unstd/: {path}"
        raise ValueError(message)
    files = {
        str(file.relative_to(path)): _digest(file)
        for file in sorted(package.rglob("*"))
        if file.is_file() and file.suffix in {".py", ".pyi", ".c", ".h", ".so", ".pyd"}
    }
    return {"path": str(path), "files_sha256": files}


def _worker(benchmark: Path, source: Path, runs: int) -> dict[str, Any]:
    environment = dict(os.environ, PYTHONPATH=str(source))
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--benchmark",
        str(benchmark),
        "--source",
        str(source),
        "--runs",
        str(runs),
    ]
    result: dict[str, Any] = json.loads(
        subprocess.check_output(command, text=True, env=environment)  # noqa: S603
    )
    imported = Path(result["environment"]["package_source"]).resolve()
    if imported != source / "unstd/serde/jsonx.py":
        message = f"requested {source}, imported {imported}"
        raise RuntimeError(message)
    return result


def _native_calls(fn: Any) -> dict[str, int]:
    """Observe public C entries without inventing nested backend events."""
    calls: Counter[str] = Counter()

    def observe(_frame: Any, event: str, target: Any) -> None:
        if event == "c_call":
            module = (
                getattr(target, "__module__", None)
                or type(getattr(target, "__self__", None)).__module__
            )
            for backend in (
                "orjson",
                "pybase64",
                "msgspec",
                "unstd.serde._json_native",
            ):
                if module == backend or module.startswith(backend + "."):
                    calls[backend] += 1

    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        if isinstance(fn, partial):
            fn.func(*fn.args, **fn.keywords)
        else:
            fn()
    finally:
        sys.setprofile(previous)
    return dict(calls)


def _workloads(benchmark: Path) -> tuple[Any, list[Any], dict[str, Any], Any]:
    """Bind the original operations to the currently selected package."""
    spec = importlib.util.spec_from_file_location(
        "_consumer_serde_workloads", benchmark
    )
    if spec is None or spec.loader is None:
        message = f"cannot import Consumer benchmark: {benchmark}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.__dict__["_native_calls"] = _native_calls
    cases, metadata = module._cases()
    from unstd.serde import jsonx

    encoder_module = (
        sys.modules.get("unstd.serde.encoding")
        or sys.modules.get("unstd.serde._internal.json_encode")
        or sys.modules.get("unstd.serde._json_encode")
    )
    native_guard = getattr(encoder_module, "_native_encode", None)
    native_spacing = native_guard
    native_facade = getattr(jsonx, "_native_dumpb", None)
    if native_facade is not None:

        def native_probe(obj: object) -> Any:
            return native_facade(obj, False, None, False)

        native_guard = native_probe
    decoder = getattr(jsonx, "_decode", None)
    decoder_module = type(getattr(decoder, "__self__", None)).__module__
    selected = []
    for case in cases:
        native = case.native
        if native == "orjson":
            if (case.name == "json-native-request" and native_guard is not None) or (
                case.name == "json-load-request"
                and getattr(jsonx.loads, "__module__", None)
                == "unstd.serde._json_native"
            ):
                native = "unstd.serde._json_native"
            elif case.name == "json-load-request" and decoder_module.startswith(
                "msgspec"
            ):
                native = "msgspec"
        selected.append(replace(case, native=native))
    native = getattr(jsonx, "_native", None)
    metadata["native_module"] = getattr(native, "__file__", None)
    binding = getattr(native, "is_bound", None)
    probes = {"encode": native_guard}
    if native_spacing is not None:
        source = next(case for case in cases if case.name == "json-default-source")
        probes["spacing"] = partial(native_spacing, source.actual.args[0], 0, True)
    readiness = getattr(native, "dispatch_ready", None)
    # Profiling restores Python frames; separate untimed dispatch evidence
    # never becomes an invented native c_call profiling event.
    for codec, function in (("loads", jsonx.loads), ("dumpb", jsonx.dumpb)):
        if binding is not None:
            probes[codec + "_bound"] = partial(binding, function)
        if readiness is not None and binding is not None and binding(function):
            probes[codec + "_ready"] = partial(readiness, function)
    return module, selected, metadata, probes


def _verify(case: Any, probes: dict[str, Any]) -> dict[str, Any]:
    expected, actual = case.reference(), case.actual()
    calls = _native_calls(case.actual)
    result = {
        "identical_output": type(actual) is type(expected) and actual == expected,
        "native_calls": calls,
        "required_native": case.native,
        "backend": "+".join(sorted(calls)) or case.fallback,
    }
    if isinstance(actual, str | bytes) and isinstance(expected, str | bytes):
        result["output_bytes"] = len(
            actual.encode() if isinstance(actual, str) else actual
        )
        result["reference_bytes"] = len(
            expected.encode() if isinstance(expected, str) else expected
        )
        with suppress(ValueError, UnicodeError):
            result["identical_json_values"] = json.loads(actual) == json.loads(expected)
    native_guard = probes.get("encode")
    if case.name == "json-native-request" and native_guard is not None:
        # Profiling establishes guard entry, not whether it accepted the tree.
        probe = native_guard(case.actual.args[0])
        result["native_guard_accepts_fixture"] = (
            type(probe) is bytes and probe == expected
        )
    native_spacing = probes.get("spacing")
    if case.name.startswith("json-default-source") and native_spacing is not None:
        # A C entry can still decline and let stdlib produce the same output.
        # Require the renderer itself to accept the real punctuation-bearing
        # fixture and return the complete independent whitespace representation.
        probe = native_spacing()
        result["native_spacing_accepts_fixture"] = (
            type(probe) is bytes and probe == expected.encode()
        )
    codec = (
        "loads"
        if case.name.startswith("json-load-")
        else "dumpb"
        if case.name.startswith(("json-native-request", "json-compact-source"))
        else None
    )
    if codec is not None:
        readiness = probes.get(codec + "_ready")
        if readiness is not None:
            result["native_function_dispatch_after_profile"] = readiness()
            # CPython refreshes instrumentation versions on its next real call.
            # Warm the public operation, checking its output again, then attest
            # the path that the timed calibration and batches actually use.
            warmed = case.actual()
            result["identical_output"] &= (
                type(warmed) is type(expected) and warmed == expected
            )
        for key, field in (
            ("bound", "native_function_vectorcall_bound"),
            ("ready", "native_function_dispatch_ready"),
        ):
            if codec + "_" + key in probes:
                result[field] = probes[codec + "_" + key]()
    return result


def _run_worker(benchmark: Path, runs: int) -> dict[str, Any]:
    """Reuse existing cases and timers, extending only native-backend evidence."""
    module, selected, metadata, native_guard = _workloads(benchmark)
    measurements = {}
    for case in selected:
        measurement = module._measure(case, runs)
        measurement.update(_verify(case, native_guard))
        measurements[case.name] = measurement
    return {"environment": metadata, "cases": measurements}


def _retained(
    benchmark: Path, source: Path, *, loader: Any = None
) -> tuple[Any, list[Any], dict[str, Any], Any]:
    """Keep source-bound callables alive while restoring the import namespace."""
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "unstd" or name.startswith("unstd.")
    }
    original_path = sys.path[:]
    try:
        for name in saved:
            del sys.modules[name]
        sys.path.insert(0, str(source))
        loaded = _workloads(benchmark) if loader is None else loader(benchmark)
        imported = Path(loaded[2]["package_source"]).resolve()
        if imported != source / "unstd/serde/jsonx.py":
            message = f"requested {source}, imported {imported}"
            raise RuntimeError(message)
        return loaded
    finally:
        for name in list(sys.modules):
            if name == "unstd" or name.startswith("unstd."):
                del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = original_path


def _paired(
    benchmark: Path,
    sources: dict[str, Path],
    runs: int,
    repetitions: int,
    select: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Alternate old/new CPU batches for each workload within one interpreter."""
    loaded = {label: _retained(benchmark, source) for label, source in sources.items()}
    reports: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    cases = {
        label: {
            case.name: case
            for case in content[1]
            if not select or any(case.name.startswith(prefix) for prefix in select)
        }
        for label, content in loaded.items()
    }
    # A warmed direct CALL can specialize differently from functools.partial.
    # Keep the original operations, and additionally exercise that public seam.
    for name, case in list(cases["after"].items()):
        if not (
            isinstance(case.actual, partial)
            and len(case.actual.args) == 1
            and not case.actual.keywords
        ):
            continue
        for collection in cases.values():
            original = collection.get(name)
            if original is not None and isinstance(original.actual, partial):
                operation, argument = original.actual.func, original.actual.args[0]

                def direct(fn: Any = operation, value: Any = argument) -> Any:
                    return fn(value)

                collection[name + "/direct"] = replace(
                    original, name=name + "/direct", actual=direct
                )
    names = cases["after"]
    if not names:
        message = "case selection matched no workloads"
        raise ValueError(message)
    fixture_hashes = {
        fixture: _digest(benchmark.parents[5] / fixture)
        for fixture in loaded["after"][2]["fixtures"]
    }
    for repetition in range(repetitions):
        measured: dict[str, dict[str, Any]] = {"before": {}, "after": {}}
        for name, current in names.items():
            previous = cases["before"].get(name)
            pair = {"before": previous, "after": current}
            evidence = {
                label: _verify(case, loaded[label][3])
                for label, case in pair.items()
                if case is not None
            }
            batch = loaded["after"][0]._batch
            iterations = 1
            while iterations < 65536:
                if batch(current.actual, iterations) * iterations >= 5_000_000:
                    break
                iterations *= 4
            samples: dict[str, list[float]] = {"before": [], "after": []}
            enabled = gc.isenabled()
            gc.disable()
            try:
                for run in range(runs):
                    labels = ("before", "after")
                    if (repetition + run) % 2:
                        labels = labels[::-1]
                    for label in labels:
                        case = pair[label]
                        if case is not None:
                            samples[label].append(batch(case.actual, iterations))
            finally:
                if enabled:
                    gc.enable()
            for label, values in samples.items():
                if values:
                    measured[label][name] = {
                        **evidence[label],
                        "cpu_ns": median(values),
                        "cpu_samples_ns": values,
                        "iterations": iterations,
                        "spread": (max(values) - min(values)) / min(values),
                    }
            if previous is not None:
                measured["after"][name]["paired_ratios"] = [
                    new / old
                    for old, new in zip(
                        samples["before"], samples["after"], strict=True
                    )
                ]
            print(f"paired {repetition + 1}/{repetitions}: {name}", file=sys.stderr)
        for label in reports:
            reports[label].append(
                {"environment": loaded[label][2], "cases": measured[label]}
            )
    if fixture_hashes != {
        fixture: _digest(benchmark.parents[5] / fixture) for fixture in fixture_hashes
    }:
        message = "Consumer fixtures changed during measurement"
        raise RuntimeError(message)
    return reports


def _summarize(
    reports: dict[str, list[dict[str, Any]]], max_regression: float | None
) -> tuple[dict[str, Any], list[str]]:
    summaries: dict[str, Any] = {}
    failures: list[str] = []
    names = reports["after"][0]["cases"]
    for name in names:
        after = [report["cases"][name] for report in reports["after"]]
        before = [report["cases"].get(name) for report in reports["before"]]
        current = median(result["cpu_ns"] for result in after)
        previous = (
            median(result["cpu_ns"] for result in before)
            if all(result is not None for result in before)
            else None
        )
        comparable = previous is not None and all(
            result["identical_output"] for result in [*before, *after]
        )
        paired_ratios = [
            ratio for result in after for ratio in result.get("paired_ratios", [])
        ]
        ratio = (
            (median(paired_ratios) if paired_ratios else current / previous)
            if comparable
            else None
        )
        summaries[name] = {
            "before_cpu_ns": previous,
            "after_cpu_ns": current,
            "after_over_before": ratio,
            "comparable": comparable,
            "identical_output": all(result["identical_output"] for result in after),
            "backends": sorted({result["backend"] for result in after}),
        }
        if paired_ratios:
            summaries[name].update(
                paired_samples=len(paired_ratios),
                paired_ratio_range=[min(paired_ratios), max(paired_ratios)],
                after_faster_samples=sum(ratio < 1 for ratio in paired_ratios),
            )
        if not all(result["identical_output"] for result in after):
            failures.append(f"{name}: output differs from independent reference")
        if any(
            result["required_native"]
            and not result["native_calls"].get(result["required_native"])
            for result in after
        ):
            failures.append(f"{name}: required native backend did not run")
        if any(
            not result.get("native_guard_accepts_fixture", True) for result in after
        ):
            failures.append(f"{name}: native guard rejected the required fixture")
        if any(
            not result.get("native_spacing_accepts_fixture", True) for result in after
        ):
            failures.append(f"{name}: native spacing rejected the required fixture")
        if any(
            not result.get("native_function_dispatch_ready", True) for result in after
        ):
            failures.append(f"{name}: native dispatch inactive after profiling probe")
        if ratio is not None and max_regression is not None and ratio > max_regression:
            failures.append(f"{name}: after/before {ratio:.3f} > {max_regression}")
        change = f"{ratio:.3f}× after/before" if ratio is not None else "not comparable"
        print(f"{name:26} {current / 1000:8.2f} µs  {change}")
    return summaries, failures


def main() -> None:
    """Run existing workers in alternating order and save their evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer-root", type=Path, default=_ROOT.parent / "consumer")
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--runs", type=int, default=9)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-regression", type=float)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--paired", action="store_true")
    parser.add_argument(
        "--select", action="append", default=[], help="case-name prefix; repeatable"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--benchmark", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.runs < 3 or args.repetitions < 1:
        parser.error("runs must be at least three and repetitions must be positive")
    if args.max_regression is not None and args.max_regression <= 0:
        parser.error("max-regression must be positive")
    benchmark = (args.consumer_root / _BENCHMARK).resolve()
    if args.worker:
        print(json.dumps(_run_worker(args.benchmark or benchmark, args.runs)))
        return
    if args.baseline_source is None or args.output is None:
        parser.error("--baseline-source and --output are required")
    benchmark_hash = _digest(benchmark)
    runner = Path(__file__).resolve()
    runner_hash = _digest(runner)
    sources = {
        "before": args.baseline_source.resolve(),
        "after": args.source.resolve(),
    }
    source_info = {label: _source(path) for label, path in sources.items()}
    reports: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    if args.paired:
        reports = _paired(benchmark, sources, args.runs, args.repetitions, args.select)
    fixtures: dict[str, str] = {}
    for repetition in range(0 if args.paired else args.repetitions):
        labels = ("before", "after") if repetition % 2 == 0 else ("after", "before")
        for label in labels:
            print(
                f"{label}: repetition {repetition + 1}/{args.repetitions}",
                file=sys.stderr,
            )
            result = _worker(benchmark, sources[label], args.runs)
            if args.select:
                result["cases"] = {
                    name: case
                    for name, case in result["cases"].items()
                    if any(name.startswith(prefix) for prefix in args.select)
                }
            fingerprints = {
                fixture: _digest(args.consumer_root / fixture)
                for fixture in result["environment"]["fixtures"]
            }
            if fixtures and fingerprints != fixtures:
                message = "Consumer fixtures changed during measurement"
                raise RuntimeError(message)
            fixtures = fingerprints
            reports[label].append(result)
    if args.paired:
        fixtures = {
            fixture: _digest(args.consumer_root / fixture)
            for fixture in reports["after"][0]["environment"]["fixtures"]
        }
    # Refuse evidence from a source edited while its workers were running.
    if source_info != {label: _source(path) for label, path in sources.items()}:
        message = "package source changed during measurement; rerun after edits finish"
        raise RuntimeError(message)
    if _digest(benchmark) != benchmark_hash:
        message = "Consumer benchmark changed during measurement"
        raise RuntimeError(message)
    if _digest(runner) != runner_hash:
        message = "comparison runner changed during measurement"
        raise RuntimeError(message)
    summaries, failures = _summarize(reports, args.max_regression)
    report = {
        "schema": 1,
        "method": {
            "clock": "process_time_ns",
            "estimator": "median paired per-batch new/old ratios"
            if args.paired
            else "median of worker medians; raw alternating native/reference CPU batches retained",
            "runs": args.runs,
            "repetitions": args.repetitions,
            "worker_order": "same interpreter; alternate versions per workload per CPU batch"
            if args.paired
            else "before/after, after/before, repeated",
            "scope": "saved Consumer fixture CPU costs; not provider or production latency",
            "comparison": "only outputs equal to their independent reference receive a release ratio",
            "distribution_metadata": "worker package versions describe installed distributions; source hashes identify selected code",
            "native_probe": "observed Python-to-C entries; a native guard's delegated orjson call is not fabricated as an orjson profiling event",
            "native_spacing": "the native spaced encoder must independently return the exact default-source reference bytes; entry followed by fallback does not satisfy this probe",
            "native_dispatch": "profiling restores Python function frames; separate untimed binding and readiness probes attest native dispatch after one output-checked warm call",
        },
        "sources": source_info,
        "benchmark": {"path": str(benchmark), "sha256": benchmark_hash},
        "runner": {"path": str(runner), "sha256": runner_hash},
        "fixtures_sha256": fixtures,
        "cases": summaries,
        "workers": reports,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.output}")
    if args.check and failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
