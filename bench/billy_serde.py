"""Compare package sources using Billy's existing, output-checked serde fixtures.

Run with the consumer's interpreter so Python and optional backends stay fixed::

    ../billy/.venv/bin/python bench/billy_serde.py \
        --baseline-source .local/billy-serde-v3/src \
        --output bench/billy-serde-results.json --check --max-regression 1.15

The original Billy benchmark owns workloads, references, native-call probes and
CPU measurement. This runner alternates isolated source workers, checks import
provenance, and retains raw reports. No Billy source or environment is changed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from functools import partial
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
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
        if file.is_file() and file.suffix in {".py", ".pyi", ".c", ".so", ".pyd"}
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


def _run_worker(benchmark: Path, runs: int) -> dict[str, Any]:
    """Reuse Billy's cases and timers, extending only native-backend evidence."""
    spec = importlib.util.spec_from_file_location("_billy_serde_workloads", benchmark)
    if spec is None or spec.loader is None:
        message = f"cannot import Billy benchmark: {benchmark}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.__dict__["_native_calls"] = _native_calls
    cases, metadata = module._cases()
    from unstd.serde import jsonx

    encoder_module = sys.modules.get("unstd.serde._json_encode")
    native_guard = getattr(encoder_module, "_native_encode", None)
    decoder = getattr(jsonx, "_decode", None)
    decoder_module = type(getattr(decoder, "__self__", None)).__module__
    selected = []
    for case in cases:
        native = case.native
        if native == "orjson":
            if case.name == "json-native-request" and native_guard is not None:
                native = "unstd.serde._json_native"
            elif case.name == "json-load-request" and decoder_module.startswith(
                "msgspec"
            ):
                native = "msgspec"
        selected.append(replace(case, native=native))
    measurements = {}
    for case in selected:
        measurement = module._measure(case, runs)
        if case.native == "unstd.serde._json_native" and native_guard is not None:
            # Profiling establishes guard entry, not whether it accepted the
            # tree. Independently prove acceptance for the required fixture.
            probe = native_guard(case.actual.args[0])
            measurement["native_guard_accepts_fixture"] = (
                type(probe) is bytes and probe == case.reference()
            )
        measurements[case.name] = measurement
    return {"environment": metadata, "cases": measurements}


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
        ratio = current / previous if comparable else None
        summaries[name] = {
            "before_cpu_ns": previous,
            "after_cpu_ns": current,
            "after_over_before": ratio,
            "comparable": comparable,
            "identical_output": all(result["identical_output"] for result in after),
            "backends": sorted({result["backend"] for result in after}),
        }
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
        if ratio is not None and max_regression is not None and ratio > max_regression:
            failures.append(f"{name}: after/before {ratio:.3f} > {max_regression}")
        change = f"{ratio:.3f}× after/before" if ratio is not None else "not comparable"
        print(f"{name:26} {current / 1000:8.2f} µs  {change}")
    return summaries, failures


def main() -> None:
    """Run existing Billy workers in alternating order and save their evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--billy-root", type=Path, default=_ROOT.parent / "billy")
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--runs", type=int, default=9)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-regression", type=float)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--benchmark", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.runs < 3 or args.repetitions < 1:
        parser.error("runs must be at least three and repetitions must be positive")
    if args.max_regression is not None and args.max_regression <= 0:
        parser.error("max-regression must be positive")
    benchmark = (args.billy_root / _BENCHMARK).resolve()
    if args.worker:
        print(json.dumps(_run_worker(args.benchmark or benchmark, args.runs)))
        return
    if args.baseline_source is None or args.output is None:
        parser.error("--baseline-source and --output are required")
    benchmark_hash = _digest(benchmark)
    sources = {
        "before": args.baseline_source.resolve(),
        "after": args.source.resolve(),
    }
    source_info = {label: _source(path) for label, path in sources.items()}
    reports: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    fixtures: dict[str, str] = {}
    for repetition in range(args.repetitions):
        labels = ("before", "after") if repetition % 2 == 0 else ("after", "before")
        for label in labels:
            print(
                f"{label}: repetition {repetition + 1}/{args.repetitions}",
                file=sys.stderr,
            )
            result = _worker(benchmark, sources[label], args.runs)
            fingerprints = {
                fixture: _digest(args.billy_root / fixture)
                for fixture in result["environment"]["fixtures"]
            }
            if fixtures and fingerprints != fixtures:
                message = "Billy fixtures changed during measurement"
                raise RuntimeError(message)
            fixtures = fingerprints
            reports[label].append(result)
    # Refuse evidence from a source edited while its workers were running.
    if source_info != {label: _source(path) for label, path in sources.items()}:
        message = "package source changed during measurement; rerun after edits finish"
        raise RuntimeError(message)
    if _digest(benchmark) != benchmark_hash:
        message = "Billy benchmark changed during measurement"
        raise RuntimeError(message)
    summaries, failures = _summarize(reports, args.max_regression)
    report = {
        "schema": 1,
        "method": {
            "clock": "process_time_ns",
            "estimator": "median of worker medians; raw alternating native/reference CPU batches retained",
            "runs": args.runs,
            "repetitions": args.repetitions,
            "worker_order": "before/after, after/before, repeated",
            "scope": "saved Billy fixture CPU costs; not provider or production latency",
            "comparison": "only outputs equal to their independent reference receive a release ratio",
            "distribution_metadata": "worker package versions describe installed distributions; source hashes identify selected code",
            "native_probe": "observed Python-to-C entries; a native guard's delegated orjson call is not fabricated as an orjson profiling event",
        },
        "sources": source_info,
        "benchmark": {"path": str(benchmark), "sha256": benchmark_hash},
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
