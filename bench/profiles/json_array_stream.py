"""Compare whole-array JSON loading with bounded streaming in fresh processes.

Run this file directly, for example::

    .venv/bin/python bench/profiles/json_array_stream.py --output /tmp/arrays.json

The parent generates deterministic JSON arrays without retaining their records.
Every measured process consumes one fixture, checksummed field by field against
independent formula-derived expectations. Both lanes read and hash the complete
same source. Peak RSS includes interpreter/backend imports; optional allocation
tracing uses additional processes, outside the ordinary timing samples.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gc
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import tracemalloc
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from typing import BinaryIO


_ROOT = Path(__file__).resolve().parents[2]
_CATEGORIES = ("answer", "tool", "refusal", "error")
_RESPONSE = "0123456789abcdef" * 12
_KEYS = {"id", "tokens", "passed", "category", "response"}
_FIELDS = struct.Struct("<QQ?")


def _receipt(path: Path) -> dict[str, Any]:
    """The exact bytes of a source, binary, or harness used by this run."""
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest}


def _source_manifest(source: Path) -> list[dict[str, Any]]:
    """Record all serde sources and binaries, including lazily imported helpers."""
    package = source / "unstd"
    paths = [package / "__init__.py"]
    paths.extend(
        path
        for path in (package / "serde").rglob("*")
        if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
    )
    return [_receipt(path) for path in sorted(paths)]


def _prepare(directory: Path, rows: int) -> dict[str, Any]:
    """Write one fixture and derive its answer directly from generator formulas."""
    path = directory / f"events-{rows}.json"
    source_digest = hashlib.sha256()
    fields_digest = hashlib.sha256()
    token_sum = 0
    category_counts = dict.fromkeys(_CATEGORIES, 0)
    source_bytes = 0
    first_record_end = 0
    with path.open("wb", buffering=1024 * 1024) as target:

        def write(raw: bytes) -> None:
            nonlocal source_bytes
            target.write(raw)
            source_digest.update(raw)
            source_bytes += len(raw)

        write(b"[")
        for index in range(rows):
            tokens = (index * 17 + 31) % 8192
            passed = index % 7 != 0
            category = _CATEGORIES[index % len(_CATEGORIES)]
            response = f"event-{index:09d}:" + _RESPONSE
            record = {
                "id": index,
                "tokens": tokens,
                "passed": passed,
                "category": category,
                "response": response,
            }
            if index:
                write(b",\n")
            write(json.dumps(record, separators=(",", ":")).encode("ascii"))
            if index == 0:
                first_record_end = source_bytes
            # These values come from the formulas above, without decoding JSON
            # or invoking the product implementation under measurement.
            fields_digest.update(_FIELDS.pack(index, tokens, passed))
            fields_digest.update(category.encode("ascii") + b"\0")
            fields_digest.update(response.encode("ascii") + b"\0")
            token_sum += tokens
            category_counts[category] += 1
        write(b"]\n")
    return {
        "path": str(path),
        "rows": rows,
        "source_bytes": source_bytes,
        "source_sha256": source_digest.hexdigest(),
        "first_record_end_bytes": first_record_end,
        "expected": {
            "rows": rows,
            "id_sum": rows * (rows - 1) // 2,
            "token_sum": token_sum,
            "passed": rows - (rows + 6) // 7,
            "category_counts": category_counts,
            "response_characters": rows * (16 + len(_RESPONSE)),
            "fields_sha256": fields_digest.hexdigest(),
        },
    }


class _CountingReader:
    """A raw binary source recording requested reads and every delivered byte."""

    def __init__(self, source: BinaryIO) -> None:
        """Wrap a file opened without buffering to avoid hidden source read-ahead."""
        self.source = source
        self.bytes_read = 0
        self.read_calls = 0
        self.unsized_read_calls = 0
        self.max_requested_bytes = 0
        self.digest = hashlib.sha256()

    def read(self, size: int = -1, /) -> bytes:
        """Read exactly as requested, counting bytes and hashing their order."""
        self.read_calls += 1
        if size < 0:
            self.unsized_read_calls += 1
        else:
            self.max_requested_bytes = max(self.max_requested_bytes, size)
        raw = self.source.read(size)
        self.bytes_read += len(raw)
        self.digest.update(raw)
        return raw


def _rss() -> dict[str, Any]:
    """Normalize macOS bytes and Linux KiB while retaining the raw RSS value."""
    if sys.platform == "win32":
        return {"bytes": None, "raw": None, "raw_unit": "resource unavailable"}
    try:
        import resource
    except ImportError:
        return {"bytes": None, "raw": None, "raw_unit": "resource unavailable"}
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return {"bytes": int(raw), "raw": raw, "raw_unit": "bytes"}
    if sys.platform.startswith("linux"):
        return {"bytes": int(raw * 1024), "raw": raw, "raw_unit": "KiB"}
    return {"bytes": None, "raw": raw, "raw_unit": "platform-dependent"}


def _worker(
    source: Path, fixture: Path, mode: str, chunk_size: int, trace: bool
) -> dict[str, Any]:
    """Consume one complete array with no fixture generation in this process."""
    sys.path.insert(0, str(source))
    from unstd.serde import jsonx

    package = (source / "unstd").resolve()
    if not Path(jsonx.__file__).resolve().is_relative_to(package):
        msg = "JSON decoder imported from outside the selected source tree"
        raise AssertionError(msg)
    gc.collect()
    rss_before = _rss()
    if trace:
        tracemalloc.start()
    count = id_sum = token_sum = passed_count = response_characters = 0
    category_counts = dict.fromkeys(_CATEGORIES, 0)
    fields_digest = hashlib.sha256()
    first_item_source_bytes = None
    first_item_wall_seconds = None
    with fixture.open("rb", buffering=0) as raw:
        reader = _CountingReader(raw)
        start_cpu = time.process_time_ns()
        start = time.perf_counter_ns()
        records = (
            jsonx.load(reader)
            if mode == "load"
            else jsonx.iterload(reader, chunk_size=chunk_size)
        )
        for record in records:
            if count == 0:
                first_item_source_bytes = reader.bytes_read
                first_item_wall_seconds = (time.perf_counter_ns() - start) / 1e9
            if type(record) is not dict or record.keys() != _KEYS:
                msg = "decoded record has an unexpected schema"
                raise AssertionError(msg)
            index = record["id"]
            tokens = record["tokens"]
            passed = record["passed"]
            category = record["category"]
            response = record["response"]
            if (
                type(index) is not int
                or type(tokens) is not int
                or type(passed) is not bool
                or type(category) is not str
                or type(response) is not str
            ):
                msg = "decoded field has an unexpected type"
                raise AssertionError(msg)
            count += 1
            id_sum += index
            token_sum += tokens
            passed_count += passed
            category_counts[category] += 1
            response_characters += len(response)
            fields_digest.update(_FIELDS.pack(index, tokens, passed))
            fields_digest.update(category.encode("ascii") + b"\0")
            fields_digest.update(response.encode("ascii") + b"\0")
        elapsed = (time.perf_counter_ns() - start) / 1e9
        elapsed_cpu = (time.process_time_ns() - start_cpu) / 1e9
        rss_peak = _rss()
        allocation_peak = tracemalloc.get_traced_memory()[1] if trace else None
        if trace:
            tracemalloc.stop()
    loaded_sources = []
    for name, module in sorted(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if name.startswith("unstd") and location is not None:
            path = Path(location)
            if path.is_file():
                if not path.resolve().is_relative_to(package):
                    msg = f"product module {name} imported outside selected source: {path}"
                    raise AssertionError(msg)
                loaded_sources.append(_receipt(path))
    backends: dict[str, str | None] = {}
    for name in ("orjson", "msgspec", "numpy"):
        try:
            backends[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            backends[name] = None
    return {
        "pid": os.getpid(),
        "mode": mode,
        "selected_source": str(source.resolve()),
        "tracemalloc_enabled": trace,
        "wall_seconds": elapsed,
        "cpu_seconds": elapsed_cpu,
        "rss_before": rss_before,
        "peak_rss": rss_peak,
        "peak_traced_bytes": allocation_peak,
        "first_item_source_bytes": first_item_source_bytes,
        "first_item_wall_seconds": first_item_wall_seconds,
        "source_bytes_read": reader.bytes_read,
        "source_sha256": reader.digest.hexdigest(),
        "read_calls": reader.read_calls,
        "unsized_read_calls": reader.unsized_read_calls,
        "max_requested_bytes": reader.max_requested_bytes,
        "summary": {
            "rows": count,
            "id_sum": id_sum,
            "token_sum": token_sum,
            "passed": passed_count,
            "category_counts": category_counts,
            "response_characters": response_characters,
            "fields_sha256": fields_digest.hexdigest(),
        },
        "loaded_sources": loaded_sources,
        "backends": backends,
    }


def _run_worker(
    source: Path,
    fixture: dict[str, Any],
    mode: str,
    chunk_size: int,
    trace: bool,
    timeout: float,
) -> dict[str, Any]:
    """Launch one bounded interpreter and independently check its complete result."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--source",
        str(source),
        "--worker-fixture",
        fixture["path"],
        "--worker-mode",
        mode,
        "--chunk-size",
        str(chunk_size),
    ]
    if trace:
        command.append("--worker-trace")
    completed = subprocess.run(  # noqa: S603 — owned interpreter and literal script
        command, capture_output=True, text=True, check=True, timeout=timeout
    )
    result = cast("dict[str, Any]", json.loads(completed.stdout))
    if result["summary"] != fixture["expected"]:
        msg = f"{mode} output differs from independently generated field expectations"
        raise AssertionError(msg)
    if (
        result["source_bytes_read"] != fixture["source_bytes"]
        or result["source_sha256"] != fixture["source_sha256"]
    ):
        msg = f"{mode} did not consume and hash the complete source"
        raise AssertionError(msg)
    if mode == "load" and result["first_item_source_bytes"] != fixture["source_bytes"]:
        msg = "whole-array control unexpectedly returned before reading its source"
        raise AssertionError(msg)
    if mode == "iterload":
        if result["unsized_read_calls"] or result["max_requested_bytes"] > chunk_size:
            msg = "streaming source reads exceeded the configured chunk size"
            raise AssertionError(msg)
        if (
            fixture["source_bytes"] > 2 * chunk_size
            and result["first_item_source_bytes"] >= fixture["source_bytes"]
        ):
            msg = (
                "streaming produced its first item only after reading the entire source"
            )
            raise AssertionError(msg)
    return result


def _summarize(fixture: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain ordinary medians and paired ratios without claiming timing floors."""
    summary: dict[str, Any] = {}
    modes = ["load", "iterload"]
    if any(run["mode"] == "iterload_before" for run in runs):
        modes.append("iterload_before")
    for mode in modes:
        samples = [run for run in runs if run["mode"] == mode]
        wall = statistics.median(run["wall_seconds"] for run in samples)
        rss = [run["peak_rss"]["bytes"] for run in samples]
        summary[mode] = {
            "median_wall_seconds": wall,
            "median_cpu_seconds": statistics.median(
                run["cpu_seconds"] for run in samples
            ),
            "median_peak_rss_bytes": (
                statistics.median(rss)
                if all(value is not None for value in rss)
                else None
            ),
            "median_first_item_wall_seconds": statistics.median(
                run["first_item_wall_seconds"] for run in samples
            ),
            "median_first_item_source_bytes": statistics.median(
                run["first_item_source_bytes"] for run in samples
            ),
            "input_mib_per_second": fixture["source_bytes"] / (1024 * 1024 * wall),
        }
    eager = summary["load"]["median_peak_rss_bytes"]
    streaming = summary["iterload"]["median_peak_rss_bytes"]
    summary["peak_rss_ratio_load_over_iterload"] = (
        eager / streaming if eager is not None and streaming else None
    )
    paired = []
    paired_slowdown = []
    paired_speedup = []
    rounds = sorted({run["round"] for run in runs})
    for round_index in rounds:
        pair = {run["mode"]: run for run in runs if run["round"] == round_index}
        paired.append(pair["load"]["wall_seconds"] / pair["iterload"]["wall_seconds"])
        paired_slowdown.append(
            pair["iterload"]["wall_seconds"] / pair["load"]["wall_seconds"]
        )
        if "iterload_before" in pair:
            paired_speedup.append(
                pair["iterload_before"]["wall_seconds"]
                / pair["iterload"]["wall_seconds"]
            )
    summary["median_paired_wall_ratio_load_over_iterload"] = statistics.median(paired)
    summary["paired_wall_ratios_iterload_over_load"] = paired_slowdown
    summary["median_paired_wall_ratio_iterload_over_load"] = statistics.median(
        paired_slowdown
    )
    if paired_speedup:
        summary["paired_wall_ratios_before_over_iterload"] = paired_speedup
        summary["median_paired_wall_speedup_over_before"] = statistics.median(
            paired_speedup
        )
    return summary


def main() -> None:
    """Save raw subprocess evidence and optional RSS gates before reporting failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument(
        "--compare-source",
        type=Path,
        help="separate source tree for the previous iterload",
    )
    parser.add_argument(
        "--rows", action="append", help="repeatable counts or comma list"
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=65536)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--tracemalloc", action="store_true")
    parser.add_argument("--min-rss-reduction", type=float)
    parser.add_argument("--max-stream-rss-growth-mib", type=float)
    parser.add_argument(
        "--max-stream-slowdown",
        type=float,
        help="maximum median paired streaming/eager wall ratio at every fixture",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-mode", choices=("load", "iterload"), help=argparse.SUPPRESS
    )
    parser.add_argument("--worker-trace", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    source = args.source.resolve()
    comparison = args.compare_source.resolve() if args.compare_source else None
    if args.chunk_size <= 0 or args.rounds <= 0 or args.timeout <= 0:
        parser.error("chunk-size, rounds and timeout must be positive")
    if args.worker_fixture is not None:
        if args.worker_mode is None:
            parser.error("worker-mode is required with worker-fixture")
        print(
            json.dumps(
                _worker(
                    source,
                    args.worker_fixture,
                    args.worker_mode,
                    args.chunk_size,
                    args.worker_trace,
                )
            )
        )
        return
    if args.output is None:
        parser.error("output is required")
    if comparison == source:
        parser.error("compare-source must select a separate source tree")
    if args.max_stream_slowdown is not None and (
        not math.isfinite(args.max_stream_slowdown) or args.max_stream_slowdown <= 0
    ):
        parser.error("max-stream-slowdown must be finite and positive")
    try:
        counts = sorted(
            {
                int(value)
                for argument in (args.rows or ["10000,100000,300000"])
                for value in argument.split(",")
            }
        )
    except ValueError:
        parser.error("rows must contain comma-separated integers")
    if not counts or counts[0] <= 0:
        parser.error("row counts must be positive")
    if args.min_rss_reduction is not None and args.min_rss_reduction <= 1:
        parser.error("min-rss-reduction must be greater than one")
    if (
        args.max_stream_rss_growth_mib is not None
        and args.max_stream_rss_growth_mib < 0
    ):
        parser.error("max-stream-rss-growth-mib must be nonnegative")
    manifest = _source_manifest(source)
    comparison_manifest = _source_manifest(comparison) if comparison else None
    report: dict[str, Any] = {
        "schema": 1,
        "utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "harness": _receipt(Path(__file__)),
        "source_manifest": manifest,
        "comparison_source_manifest": comparison_manifest,
        "configuration": {
            "rows": counts,
            "rounds": args.rounds,
            "chunk_size": args.chunk_size,
            "tracemalloc_supplement": args.tracemalloc,
            "min_rss_reduction": args.min_rss_reduction,
            "max_stream_rss_growth_mib": args.max_stream_rss_growth_mib,
            "max_stream_slowdown": args.max_stream_slowdown,
            "source": str(source),
            "compare_source": str(comparison) if comparison else None,
        },
        "method": (
            "fresh interpreter per sample; opposing lane order in alternating rounds; "
            "same deterministic ASCII arrays; source reading, SHA256 and complete "
            "field aggregation inside timing; GC enabled; fixture creation and "
            "source receipts outside timing; optional tracing in separate workers"
        ),
        "limitations": (
            "synthetic event records on one host; OS file cache is uncontrolled; "
            "RSS includes interpreter and backend imports; first-item source bytes "
            "measure the raw source API, not physical disk traffic; timing includes "
            "aggregation and hashing and is not an isolated decoder throughput claim; "
            "memory depends on the largest individual item and configured chunk size"
        ),
        "fixtures": [],
        "failures": [],
    }
    try:
        with TemporaryDirectory(prefix="unstd-array-stream-") as temporary:
            directory = Path(temporary)
            for position, rows in enumerate(counts):
                fixture = _prepare(directory, rows)
                fixture["runs"] = []
                fixture["allocation_runs"] = []
                report["fixtures"].append(fixture)
                for round_index in range(args.rounds):
                    order: tuple[str, ...] = ("load", "iterload")
                    if comparison is not None:
                        order = ("load", "iterload", "iterload_before")
                    if (position + round_index) % 2:
                        order = tuple(reversed(order))
                    for order_index, mode in enumerate(order):
                        selected_source = (
                            comparison if mode == "iterload_before" else source
                        )
                        assert selected_source is not None
                        worker_mode = "iterload" if mode == "iterload_before" else mode
                        run = _run_worker(
                            selected_source,
                            fixture,
                            worker_mode,
                            args.chunk_size,
                            False,
                            args.timeout,
                        )
                        run.update(
                            {"round": round_index, "order": order_index, "mode": mode}
                        )
                        fixture["runs"].append(run)
                fixture["summary"] = _summarize(fixture, fixture["runs"])
                if args.tracemalloc:
                    trace_modes = (
                        ("load", "iterload")
                        if comparison is None
                        else ("load", "iterload", "iterload_before")
                    )
                    for mode in trace_modes:
                        selected_source = (
                            comparison if mode == "iterload_before" else source
                        )
                        assert selected_source is not None
                        worker_mode = "iterload" if mode == "iterload_before" else mode
                        run = _run_worker(
                            selected_source,
                            fixture,
                            worker_mode,
                            args.chunk_size,
                            True,
                            args.timeout,
                        )
                        run["mode"] = mode
                        fixture["allocation_runs"].append(run)
                if args.max_stream_slowdown is not None and (
                    fixture["summary"]["median_paired_wall_ratio_iterload_over_load"]
                    > args.max_stream_slowdown
                ):
                    report["failures"].append(
                        f"{rows} rows exceeded the requested streaming/eager wall ratio"
                    )
                # Keep only one generated export on disk at any time. Its byte
                # length, source digest and generation formulas remain in evidence.
                Path(fixture["path"]).unlink()
        after_manifest = _source_manifest(source)
        report["source_manifest_verified_unchanged"] = after_manifest == manifest
        if after_manifest != manifest:
            report["failures"].append("product sources changed during measurement")
        if comparison is not None:
            comparison_after = _source_manifest(comparison)
            report["comparison_manifest_verified_unchanged"] = (
                comparison_after == comparison_manifest
            )
            if comparison_after != comparison_manifest:
                report["failures"].append(
                    "comparison product sources changed during measurement"
                )
        smallest, largest = report["fixtures"][0], report["fixtures"][-1]
        small_rss = smallest["summary"]["iterload"]["median_peak_rss_bytes"]
        large_rss = largest["summary"]["iterload"]["median_peak_rss_bytes"]
        growth = large_rss - small_rss if small_rss is not None else None
        report["stream_peak_rss_growth_bytes"] = growth
        ratio = largest["summary"]["peak_rss_ratio_load_over_iterload"]
        if args.min_rss_reduction is not None and (
            ratio is None or ratio < args.min_rss_reduction
        ):
            report["failures"].append(
                "largest fixture missed the requested RSS reduction"
            )
        if args.max_stream_rss_growth_mib is not None and (
            growth is None or growth > args.max_stream_rss_growth_mib * 1024 * 1024
        ):
            report["failures"].append(
                "streaming RSS growth exceeded the requested bound"
            )
    except (AssertionError, OSError, subprocess.SubprocessError, ValueError) as error:
        report["failures"].append(f"{type(error).__name__}: {error}")
        if isinstance(error, subprocess.CalledProcessError):
            report["worker_stderr"] = error.stderr[-16000:]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for fixture in report["fixtures"]:
        if "summary" not in fixture:
            continue
        summary = fixture["summary"]
        print(
            f"{fixture['rows']:,} rows / {fixture['source_bytes'] / 2**20:.1f} MiB: "
            f"load {summary['load']['median_wall_seconds']:.3f}s, "
            f"iterload {summary['iterload']['median_wall_seconds']:.3f}s, "
            f"RSS ratio {summary['peak_rss_ratio_load_over_iterload']}"
        )
    print(f"Evidence: {args.output.resolve()}")
    if report["failures"]:
        raise SystemExit("; ".join(report["failures"]))


if __name__ == "__main__":
    main()
