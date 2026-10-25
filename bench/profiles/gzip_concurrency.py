"""Concurrent independent gzip streams over the frozen Billy consumer fixtures.

Run this file directly. Each measured process selects its installation before
importing the benchmark or product packages. Executors and threads are warmed
outside timing; each job repeatedly opens its own stream. Aggregate throughput
counts the bytes delivered by the consumer, including both upload passes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import gc
import hashlib
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
_MIN_BATCH_NS = 25_000_000
_MAX_BATCH_NS = 250_000_000
_MAX_ITERATIONS = 4096


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _workers(value: str) -> tuple[int, ...]:
    """A small explicit worker set, bounded independently of machine capacity."""
    try:
        counts = tuple(int(part) for part in value.split(","))
    except ValueError as error:
        message = "workers must be comma-separated integers from 1 through 4"
        raise argparse.ArgumentTypeError(message) from error
    if (
        not counts
        or len(set(counts)) != len(counts)
        or any(count < 1 or count > 4 for count in counts)
    ):
        message = "workers must be unique integers from 1 through 4"
        raise argparse.ArgumentTypeError(message)
    return counts


def _payload_bytes(operation: str, raw_size: int, budget: int) -> int:
    """The consumer's logical uncompressed byte work per complete operation."""
    if operation == "upload":
        return raw_size * 2
    if operation == "bounded":
        return min(raw_size, budget + 1)
    return raw_size


def _rates(
    wall_ns: int, cpu_ns: int, workers: int, iterations: int, payload_bytes: int
) -> dict[str, int | float]:
    """Normalize the complete batch, including every independent worker job."""
    if wall_ns <= 0 or workers < 1 or iterations < 1 or payload_bytes < 0:
        message = "invalid concurrency measurement divisor"
        raise ValueError(message)
    operations = workers * iterations
    return {
        "wall_ns": wall_ns,
        "process_cpu_ns": cpu_ns,
        "operations": operations,
        "logical_bytes": payload_bytes * operations,
        "operations_per_second": operations * 1_000_000_000 / wall_ns,
        "logical_bytes_per_second": payload_bytes
        * operations
        * 1_000_000_000
        / wall_ns,
        "wall_ns_per_operation": wall_ns / operations,
    }


def _repeat(function: Callable[[], object], iterations: int) -> object:
    result: object = None
    for _ in range(iterations):
        result = function()
    return result


def _batch(
    executor: ThreadPoolExecutor,
    function: Callable[[], object],
    workers: int,
    iterations: int,
) -> tuple[int, int, list[object]]:
    cpu_start = time.process_time_ns()
    wall_start = time.perf_counter_ns()
    pending = [executor.submit(_repeat, function, iterations) for _ in range(workers)]
    results = [future.result() for future in pending]
    return (
        time.perf_counter_ns() - wall_start,
        time.process_time_ns() - cpu_start,
        results,
    )


def _measure(
    function: Callable[[], object],
    check: Callable[[object], None],
    *,
    workers: int,
    rounds: int,
    payload_bytes: int,
) -> dict[str, Any]:
    """Warm every executor thread, then calibrate and retain bounded batches."""
    check(function())
    with ThreadPoolExecutor(max_workers=workers) as executor:
        ready = threading.Barrier(workers)

        def warm() -> object:
            ready.wait(timeout=10)
            return function()

        pending = [executor.submit(warm) for _ in range(workers)]
        for future in pending:
            check(future.result())
        iterations = 1
        enabled = gc.isenabled()
        gc.disable()
        try:
            while True:
                wall, cpu, results = _batch(executor, function, workers, iterations)
                for result in results:
                    check(result)
                if wall >= _MIN_BATCH_NS or iterations == _MAX_ITERATIONS:
                    break
                iterations = min(
                    _MAX_ITERATIONS,
                    iterations * max(2, min(8, _MIN_BATCH_NS // max(1, wall))),
                )
            calibration = _rates(wall, cpu, workers, iterations, payload_bytes)
            if wall > _MAX_BATCH_NS and iterations > 1:
                iterations = max(1, int(iterations * _MAX_BATCH_NS / wall))
            samples = []
            for _ in range(rounds):
                wall, cpu, results = _batch(executor, function, workers, iterations)
                samples.append(_rates(wall, cpu, workers, iterations, payload_bytes))
                for result in results:
                    check(result)
        finally:
            if enabled:
                gc.enable()
    check(function())
    return {
        "workers": workers,
        "iterations_per_job": iterations,
        "logical_bytes_per_operation": payload_bytes,
        "calibration": calibration,
        "samples": samples,
        "median_logical_bytes_per_second": statistics.median(
            sample["logical_bytes_per_second"] for sample in samples
        ),
    }


def _require_native(calls: dict[str, int], operation: str) -> None:
    """A checksum alone cannot establish that an actual native codec ran."""
    suffixes = (
        (".compress",)
        if operation == "journal-write"
        else (
            ".decompress",
            ".read",
            ".readinto",
        )
    )
    if not any(
        count > 0
        and name.startswith(("zlib_ng", "unstd.compress._gzip_native"))
        and name.endswith(suffixes)
        for name, count in calls.items()
    ):
        message = "the concurrent workload did not execute a native gzip codec"
        raise RuntimeError(message)


def _worker(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    source = source / "src" if (source / "src/unstd").exists() else source
    sys.path.insert(0, str(source))
    codec = importlib.import_module("unstd.compress.gzipx")
    if codec.__file__ is None or Path(codec.__file__).resolve() != (
        source / "unstd/compress/gzipx.py"
    ):
        message = "requested gzip source was not imported"
        raise RuntimeError(message)
    # Imports above must precede bench.__init__, which can import product code.
    from bench.profiles import billy_gzip, gzip_reads

    cases, manifest = billy_gzip._load_fixtures(args.fixtures)
    selected = [case for case in cases if case.name == args.case]
    if len(selected) != 1:
        message = f"expected one frozen Billy case: {args.case}"
        raise ValueError(message)
    case = selected[0]
    function = billy_gzip._operation(codec, case)

    def check(actual: object) -> None:
        billy_gzip._validate(codec, case, actual)

    check(function())
    calls = gzip_reads._native_calls(function)
    _require_native(calls, case.operation)
    if sys.getprofile() is not None or sys.gettrace() is not None:
        message = "concurrency timing requires tracing and profiling disabled"
        raise RuntimeError(message)
    measured = _measure(
        function,
        check,
        workers=args.workers,
        rounds=args.rounds,
        payload_bytes=_payload_bytes(case.operation, len(case.raw), case.budget),
    )
    product_sources = {}
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith("unstd.") and path is not None:
            path = Path(path).resolve()
            if path.is_file() and path.is_relative_to(source):
                product_sources[str(path)] = _sha(path.read_bytes())
    return {
        "source": str(source),
        "case": case.name,
        "operation": case.operation,
        "raw_bytes": len(case.raw),
        "raw_sha256": _sha(case.raw),
        "packed_sha256": _sha(case.packed),
        "native_calls": calls,
        "measurement": measured,
        "metadata": gzip_reads._metadata(
            codec, [gzip_reads._Case(case.name, case.raw, case.packed)]
        ),
        "ijson": version("ijson") if case.operation == "query" else None,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "python_executable": sys.executable,
            "cpu_count": os.cpu_count(),
            "cpu_affinity": sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None,
            "loadavg": os.getloadavg(),
        },
        "product_sources": product_sources,
        "source_contracts": manifest["source_contracts"],
    }


def _run(args: argparse.Namespace) -> None:
    sources = {"candidate": args.source}
    if args.baseline_source is not None:
        sources["baseline"] = args.baseline_source
    records: list[dict[str, Any]] = []
    for repetition in range(args.rounds):
        counts = args.workers if repetition % 2 == 0 else args.workers[::-1]
        for workers in counts:
            variants = list(sources)
            if repetition % 2:
                variants.reverse()
            for variant in variants:
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "worker",
                    "--source",
                    str(sources[variant].resolve()),
                    "--fixtures",
                    str(args.fixtures.resolve()),
                    "--case",
                    args.case,
                    "--workers",
                    str(workers),
                    "--rounds",
                    "1",
                ]
                completed = subprocess.run(  # noqa: S603 — this interpreter and trusted explicit source/fixture paths
                    command, capture_output=True, text=True, check=True, timeout=180
                )
                result = json.loads(completed.stdout)
                if result["measurement"]["workers"] != workers:
                    message = "worker count differs from requested concurrency"
                    raise RuntimeError(message)
                records.append({"variant": variant, "round": repetition, **result})
    reference = records[0]
    for record in records:
        for name in ("case", "raw_sha256", "packed_sha256", "source_contracts"):
            if record[name] != reference[name]:
                message = f"concurrency workers used different {name}"
                raise RuntimeError(message)
    summary = {
        variant: {
            str(workers): statistics.median(
                sample["logical_bytes_per_second"]
                for record in records
                if record["variant"] == variant
                and record["measurement"]["workers"] == workers
                for sample in record["measurement"]["samples"]
            )
            for workers in args.workers
        }
        for variant in sources
    }
    report = {
        "diagnostic_only": True,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "harness_sha256": _sha(Path(__file__).read_bytes()),
        "fixture_manifest_sha256": _sha(args.fixtures.read_bytes()),
        "throughput_unit": "logical uncompressed bytes per second; upload counts both passes",
        "records": records,
        "median_logical_bytes_per_second": summary,
        "candidate_speedup": {
            str(workers): summary["candidate"][str(workers)]
            / summary["baseline"][str(workers)]
            for workers in args.workers
        }
        if "baseline" in summary
        else {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}))


def main() -> None:
    """Measure isolated independent-stream throughput on bounded worker counts."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--fixtures", type=Path, required=True)
        command.add_argument("--case", default="dataset-upload-40001")
        command.add_argument("--rounds", type=int, default=7)
        if name == "run":
            command.add_argument("--baseline-source", type=Path)
            command.add_argument("--workers", type=_workers, default=(1, 2, 4))
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--workers", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 31:
        parser.error("rounds must be between 1 and 31")
    if args.command == "worker":
        if not 1 <= args.workers <= 4:
            parser.error("workers must be between 1 and 4")
        print(json.dumps(_worker(args)))
    else:
        _run(args)


if __name__ == "__main__":
    main()
