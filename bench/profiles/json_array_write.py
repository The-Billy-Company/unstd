"""Compare generated JSON exports through streaming and eager public APIs.

Run this file directly with ``--output /tmp/json-array-write.json``. Each sample
uses a fresh process, generates every record inside its timer, writes a real
file and flushes it. The parent derives the exact complete output digest with
stdlib JSON independently of the product. Output hashing follows timing and RSS
sampling, and cannot inflate the reported write-time peak memory.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gc
from hashlib import file_digest, sha256
from importlib import metadata
import io
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any, cast, override


if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import BinaryIO, TextIO


_ROOT = Path(__file__).resolve().parents[2]
_MODES = ("dump_array", "jsonx_dump", "stdlib_dump", "dumpb")
_RESPONSE = 'A bounded export: café 🪵; punctuation ["a", {"b": 2}]. ' * 4


def _record(index: int) -> dict[str, Any]:
    """One independently allocated event with nested values and finite floats."""
    return {
        "id": index,
        "response": f"event-{index:09d}: {_RESPONSE}",
        "metrics": {
            "tokens": (index * 17 + 31) % 8192,
            "score": (index % 97) / 97,
            "passed": index % 7 != 0,
        },
        "tags": ["answer", f"group-{index % 11}"],
    }


def _receipt(path: Path) -> dict[str, Any]:
    """Retain the actual source or binary's identity without loading its bytes."""
    with path.open("rb") as source:
        digest = file_digest(source, "sha256").hexdigest()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest}


def _manifest(source: Path) -> list[dict[str, Any]]:
    """Identify every serde implementation file and the benchmark driver."""
    paths = [Path(__file__).resolve()]
    paths.extend(
        path
        for path in (source / "unstd/serde").rglob("*")
        if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
    )
    return [_receipt(path) for path in sorted(paths)]


def _expected(rows: int) -> dict[str, Any]:
    """Derive exact bytes from stdlib row encodings without building an array."""
    digest = sha256()
    length = 0

    def absorb(raw: bytes) -> None:
        nonlocal length
        digest.update(raw)
        length += len(raw)

    absorb(b"[")
    for index in range(rows):
        if index:
            absorb(b",")
        raw = json.dumps(
            _record(index), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        absorb(raw)
    absorb(b"]")
    return {"rows": rows, "output_bytes": length, "output_sha256": digest.hexdigest()}


def _rss() -> dict[str, Any]:
    """Normalize platform-specific process high-water RSS without subtraction."""
    if sys.platform == "win32":
        return {"bytes": None, "raw": None, "unit": "unavailable"}
    try:
        import resource
    except ImportError:
        return {"bytes": None, "raw": None, "unit": "unavailable"}
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return {"bytes": int(raw), "raw": raw, "unit": "bytes"}
    if sys.platform.startswith("linux"):
        return {"bytes": int(raw * 1024), "raw": raw, "unit": "KiB"}
    return {"bytes": None, "raw": raw, "unit": "platform-dependent"}


class _Records:
    """A single-pass source reporting how far the writer has consumed it."""

    def __init__(self, count: int) -> None:
        """Initialize the requested corpus length without allocating its rows."""
        self.count = count
        self.consumed = 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Generate all record fields inside the measured pipeline."""
        for index in range(self.count):
            self.consumed += 1
            yield _record(index)


class _Writes:
    """Small shared write instrumentation used by text and binary wrappers."""

    def __init__(self, records: _Records, first_record_end: int | None) -> None:
        """Track calls, maximum requested size and first-output consumption."""
        self.records = records
        self.calls = self.max_requested_units = 0
        self.first_consumed: int | None = None
        self.first_ns: int | None = None
        self.first_record_end = first_record_end
        self.accepted_units = 0
        self.first_record_consumed: int | None = None
        self.first_record_ns: int | None = None

    def observe(self, units: int) -> None:
        """Record each request without hashing or transcoding its payload."""
        if self.calls == 0:
            self.first_consumed = self.records.consumed
            self.first_ns = time.perf_counter_ns()
        self.calls += 1
        self.max_requested_units = max(self.max_requested_units, units)

    def accept(self, units: int) -> None:
        """Mark when the complete first row has reached the underlying stream."""
        self.accepted_units += units
        if (
            self.first_record_end is not None
            and self.first_record_ns is None
            and self.accepted_units >= self.first_record_end
        ):
            self.first_record_consumed = self.records.consumed
            self.first_record_ns = time.perf_counter_ns()


class _BinarySink:
    """Count real binary file writes without adding an output-sized copy."""

    def __init__(self, target: BinaryIO, writes: _Writes) -> None:
        """Wrap the same file buffering used by the eager byte writer."""
        self.target, self.writes = target, writes

    def write(self, value: bytes, /) -> int:
        """Forward exactly the bytes requested by the public API."""
        self.writes.observe(len(value))
        written = self.target.write(value)
        self.writes.accept(written)
        return written


class _TextSink(io.TextIOBase):
    """A text stream counted in characters, as text write contracts require."""

    def __init__(self, target: TextIO, writes: _Writes) -> None:
        """Wrap UTF-8 output without changing JSON's individual write calls."""
        self.target, self.writes = target, writes

    @override
    def write(self, value: str, /) -> int:
        """Forward text; the underlying stream performs the only UTF-8 encoding."""
        self.writes.observe(len(value))
        written = self.target.write(value)
        self.writes.accept(written)
        return written


def _worker(
    source: Path,
    output: Path,
    rows: int,
    mode: str,
    chunk_size: int,
    *,
    diagnostic: bool,
) -> dict[str, Any]:
    """Measure one generated export, then independently hash its complete file."""
    sys.path.insert(0, str(source))
    from unstd.serde import jsonx

    if Path(jsonx.__file__).resolve() != source / "unstd/serde/jsonx.py":
        message = "JSON writer imported outside selected source tree"
        raise AssertionError(message)
    before = _manifest(source)
    gc.collect()
    records = _Records(rows)
    first_record = json.dumps(_record(0), ensure_ascii=False, separators=(",", ":"))
    first_record_units = (
        len(first_record.encode("utf-8"))
        if mode in {"dump_array", "dumpb"}
        else len(first_record)
    )
    writes = _Writes(records, 1 + first_record_units if rows else None)
    rss_before = _rss()
    with output.open("wb") as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
        sink = _BinarySink(binary, writes) if diagnostic else binary
        text_sink = _TextSink(text, writes) if diagnostic else text
        started = time.perf_counter_ns()
        cpu_started = time.process_time_ns()
        if mode == "dump_array":
            written_rows = jsonx.dump_array(records, sink, chunk_size=chunk_size)
        else:
            materialized = list(records)
            if mode == "dumpb":
                raw = jsonx.dumpb(materialized)
                sink.write(raw)
            else:
                encode = jsonx.dump if mode == "jsonx_dump" else json.dump
                encode(
                    materialized,
                    text_sink,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            written_rows = len(materialized)
        text.flush()
        binary.flush()
        elapsed = time.perf_counter_ns() - started
        cpu_elapsed = time.process_time_ns() - cpu_started
        peak = _rss()
        text.detach()
    with output.open("rb") as completed:
        output_hash = file_digest(completed, "sha256").hexdigest()
    if _manifest(source) != before:
        message = "JSON product source changed during measured export"
        raise AssertionError(message)
    backends: dict[str, str | None] = {}
    for name in ("orjson", "msgspec", "numpy"):
        try:
            backends[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            backends[name] = None
    return {
        "pid": os.getpid(),
        "mode": mode,
        "instrumented_diagnostic": diagnostic,
        "rows": rows,
        "rows_generated": records.consumed,
        "rows_written": written_rows,
        "wall_seconds": elapsed / 1e9,
        "cpu_seconds": cpu_elapsed / 1e9,
        "gc_enabled": gc.isenabled(),
        "rss_before": rss_before,
        "peak_rss": peak,
        "write_calls": writes.calls if diagnostic else None,
        "max_requested_units": writes.max_requested_units if diagnostic else None,
        "write_unit": "bytes" if mode in {"dump_array", "dumpb"} else "characters",
        "rows_consumed_at_first_write": writes.first_consumed,
        "first_write_seconds": (
            None if writes.first_ns is None else (writes.first_ns - started) / 1e9
        ),
        "rows_consumed_at_first_complete_record": writes.first_record_consumed,
        "first_complete_record_seconds": (
            None
            if writes.first_record_ns is None
            else (writes.first_record_ns - started) / 1e9
        ),
        "output_bytes": output.stat().st_size,
        "output_sha256": output_hash,
        "source_manifest": before,
        "backends": backends,
    }


def _run_worker(
    source: Path,
    output: Path,
    expected: dict[str, Any],
    mode: str,
    chunk_size: int,
    timeout: float,
    *,
    diagnostic: bool = False,
) -> dict[str, Any]:
    """Validate every fresh process against an independent complete-byte oracle."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--source",
        str(source),
        "--worker-output",
        str(output),
        "--worker-rows",
        str(expected["rows"]),
        "--worker-mode",
        mode,
        "--chunk-size",
        str(chunk_size),
    ]
    if diagnostic:
        command.append("--worker-diagnostic")
    completed = subprocess.run(  # noqa: S603 — owned interpreter and benchmark driver
        command, capture_output=True, text=True, check=True, timeout=timeout
    )
    result = cast("dict[str, Any]", json.loads(completed.stdout))
    for key in ("output_bytes", "output_sha256"):
        if result[key] != expected[key]:
            message = f"{mode} failed independent complete-output check: {key}"
            raise AssertionError(message)
    if (
        result["rows_written"] != expected["rows"]
        or result["rows_generated"] != expected["rows"]
    ):
        message = f"{mode} did not generate and write every requested row"
        raise AssertionError(message)
    if mode == "dump_array" and diagnostic:
        if result["max_requested_units"] > chunk_size:
            message = "streaming exceeded its configured binary write bound"
            raise AssertionError(message)
        if (
            expected["output_bytes"] > 2 * chunk_size
            and result["rows_consumed_at_first_complete_record"] >= expected["rows"]
        ):
            message = "streaming consumed its complete source before first complete row"
            raise AssertionError(message)
    return result


def _summary(
    runs: list[dict[str, Any]], diagnostics: list[dict[str, Any]]
) -> dict[str, Any]:
    """Retain per-mode medians and explicit eager/streaming ratios."""
    modes: dict[str, dict[str, Any]] = {}
    for mode in _MODES:
        samples = [run for run in runs if run["mode"] == mode]
        diagnostic = next(run for run in diagnostics if run["mode"] == mode)
        rss = [sample["peak_rss"]["bytes"] for sample in samples]
        modes[mode] = {
            "median_wall_seconds": median(sample["wall_seconds"] for sample in samples),
            "median_cpu_seconds": median(sample["cpu_seconds"] for sample in samples),
            "median_peak_rss_bytes": median(rss)
            if all(value is not None for value in rss)
            else None,
            "diagnostic_write_calls": diagnostic["write_calls"],
            "diagnostic_first_write_seconds": diagnostic["first_write_seconds"],
            "diagnostic_rows_consumed_at_first_write": diagnostic[
                "rows_consumed_at_first_write"
            ],
            "diagnostic_first_complete_record_seconds": diagnostic[
                "first_complete_record_seconds"
            ],
            "diagnostic_rows_consumed_at_first_complete_record": diagnostic[
                "rows_consumed_at_first_complete_record"
            ],
        }
    stream = modes["dump_array"]
    comparisons: dict[str, dict[str, float | None]] = {}
    for mode in _MODES[1:]:
        other = modes[mode]
        rss = other["median_peak_rss_bytes"]
        stream_rss = stream["median_peak_rss_bytes"]
        comparisons[mode] = {
            "eager_over_stream_wall": other["median_wall_seconds"]
            / stream["median_wall_seconds"],
            "eager_over_stream_rss": rss / stream_rss
            if rss is not None and stream_rss is not None
            else None,
            "eager_over_stream_write_calls": other["diagnostic_write_calls"]
            / stream["diagnostic_write_calls"],
        }
    return {"modes": modes, "comparisons": comparisons}


def main() -> None:
    """Measure increasing generated corpora with retained fresh-process samples."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--rows", default="1000,10000,100000")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=65536)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-rss-reduction", type=float)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-rows", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--worker-mode", choices=_MODES, help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-diagnostic", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    source = args.source.resolve()
    if args.chunk_size < 1 or args.rounds < 1 or args.timeout <= 0:
        parser.error("chunk size, rounds and timeout must be positive")
    if args.worker_output is not None:
        if args.worker_rows is None or args.worker_mode is None:
            parser.error("worker output requires worker rows and mode")
        print(
            json.dumps(
                _worker(
                    source,
                    args.worker_output,
                    args.worker_rows,
                    args.worker_mode,
                    args.chunk_size,
                    diagnostic=args.worker_diagnostic,
                )
            )
        )
        return
    try:
        counts = sorted({int(value) for value in args.rows.split(",")})
    except ValueError:
        parser.error("--rows must contain comma-separated nonnegative integers")
    if not counts or counts[0] < 0:
        parser.error("--rows must contain nonnegative integers")
    if args.output is None:
        parser.error("--output is required")
    if args.min_rss_reduction is not None and args.min_rss_reduction < 1:
        parser.error("--min-rss-reduction must be at least one")
    before = _manifest(source)
    cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="unstd-json-array-write-") as temporary:
        for rows in counts:
            expected = _expected(rows)
            runs = []
            for repetition in range(args.rounds):
                # Rotate each mode through different slots, reversing alternate
                # repetitions. All sample processes are serial on the same host.
                shift = repetition % len(_MODES)
                order = list(_MODES[shift:] + _MODES[:shift])
                if repetition % 2:
                    order.reverse()
                for mode in order:
                    path = Path(temporary) / f"{rows}-{repetition}-{mode}.json"
                    result = _run_worker(
                        source, path, expected, mode, args.chunk_size, args.timeout
                    )
                    result["repetition"] = repetition
                    result["order"] = order
                    runs.append(result)
                    path.unlink()
                print(
                    f"Measured {rows:,} rows, repetition {repetition + 1}",
                    file=sys.stderr,
                )
            diagnostics = []
            for mode in _MODES:
                path = Path(temporary) / f"{rows}-diagnostic-{mode}.json"
                diagnostics.append(
                    _run_worker(
                        source,
                        path,
                        expected,
                        mode,
                        args.chunk_size,
                        args.timeout,
                        diagnostic=True,
                    )
                )
                path.unlink()
            cases.append(
                {
                    "expected": expected,
                    "summary": _summary(runs, diagnostics),
                    "runs": runs,
                    "diagnostics": diagnostics,
                }
            )
    if _manifest(source) != before:
        message = "JSON product source changed between benchmark samples"
        raise AssertionError(message)
    failures = []
    if args.min_rss_reduction is not None:
        observed = cases[-1]["summary"]["comparisons"]["jsonx_dump"][
            "eager_over_stream_rss"
        ]
        if observed is None or observed < args.min_rss_reduction:
            failures.append(
                f"largest-corpus peak-RSS ratio {observed} below {args.min_rss_reduction}"
            )
    report = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "source": str(source),
        "chunk_size": args.chunk_size,
        "rounds": args.rounds,
        "method": {
            "timer_includes": "record generation, list materialization where required, encoding, uninstrumented real file writes and flush",
            "timer_excludes": "imports, opening output file, complete output hashing and source receipts",
            "peak_rss": "complete fresh-process high-water mark sampled before output verification; no baseline subtraction",
            "reference": "independent parent stdlib per-row encoder plus literal array framing; exact complete byte count and SHA256",
            "write_instrumentation": "separate fresh diagnostic processes only; ordinary timing/RSS samples use actual file handles with no write wrappers. Diagnostics count calls, first complete record and maximum request; their elapsed time is never used for primary speed ratios",
            "filesystem": "buffered real files, OS cache uncontrolled, no fsync",
            "lanes": {
                "dump_array": "generator -> jsonx.dump_array -> binary file",
                "jsonx_dump": "list(generator) -> jsonx.dump -> UTF-8 text file",
                "stdlib_dump": "list(generator) -> json.dump -> UTF-8 text file",
                "dumpb": "list(generator) -> jsonx.dumpb -> binary file",
            },
        },
        "source_manifest": before,
        "cases": cases,
        "min_rss_reduction": args.min_rss_reduction,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence saved to {args.output}")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
