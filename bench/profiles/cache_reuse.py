"""Prove persisted reuse of compressed eval summaries in fresh processes.

Run ``python -m bench.profiles.cache_reuse --output /tmp/cache-reuse.json``.
Fixtures and independent expected summaries are prepared outside the timer.
Both workers verify every result, while the parent checks distinct process IDs,
producer counts, and output digests. No sleeps simulate expensive work.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any, cast

import unstd.cache as cache_module
from unstd.cache import Cache
from unstd.compress import gzipx
from unstd.serde import ndjson


if TYPE_CHECKING:
    from unstd.serde.jsonx import Json


def _digest(value: object) -> str:
    """A stdlib digest independent of the cache's encoding implementation."""
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _prepare(directory: Path, batches: int, rows: int) -> dict[str, int]:
    """Synthetic eval exports with independently accumulated expected results."""
    expected = []
    compressed_bytes = 0
    uncompressed_bytes = 0
    for batch in range(batches):
        records = []
        tokens = 0
        passed = 0
        labels = dict.fromkeys(("answer", "tool", "refusal", "error"), 0)
        for index in range(rows):
            label = ("answer", "tool", "refusal", "error")[(index + batch) % 4]
            usage = 64 + (index * 17 + batch * 11) % 4096
            success = (index + batch) % 7 != 0
            records.append(
                {
                    "id": f"eval-{batch:04d}-{index:06d}",
                    "label": label,
                    "passed": success,
                    "tokens": usage,
                    "response": sha256(f"{batch}:{index}".encode()).hexdigest() * 4,
                }
            )
            tokens += usage
            passed += success
            labels[label] += 1
        raw = b"\n".join(json.dumps(row).encode() for row in records) + b"\n"
        payload = gzip.compress(raw, mtime=0)
        (directory / f"batch-{batch}.jsonl.gz").write_bytes(payload)
        compressed_bytes += len(payload)
        uncompressed_bytes += len(raw)
        expected.append(
            {"rows": rows, "tokens": tokens, "passed": passed, "labels": labels}
        )
    (directory / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    return {
        "batches": batches,
        "rows_per_batch": rows,
        "total_rows": batches * rows,
        "compressed_bytes": compressed_bytes,
        "uncompressed_bytes": uncompressed_bytes,
    }


def _summarize(path: Path) -> Json:
    """Decode and aggregate actual compressed records through existing unstd APIs."""
    records = cast(
        "list[dict[str, Any]]", ndjson.loads(gzipx.decompress(path.read_bytes()))
    )
    labels: dict[str, Json] = {}
    for record in records:
        label = record["label"]
        labels[label] = cast("int", labels.get(label, 0)) + 1
    return {
        "rows": len(records),
        "tokens": sum(record["tokens"] for record in records),
        "passed": sum(record["passed"] for record in records),
        "labels": labels,
    }


def _worker(directory: Path) -> dict[str, Any]:
    """One complete application run, independently verifying all output values."""
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    calls = 0
    results = []
    start = time.perf_counter()
    cache = Cache(directory / "results.sqlite3", namespace="eval-summary-v1")
    for batch in range(len(expected)):

        def produce(index: int = batch) -> Json:
            nonlocal calls
            calls += 1
            return _summarize(directory / f"batch-{index}.jsonl.gz")

        results.append(cache.get_or_compute(f"batch:{batch}", produce))
    elapsed = time.perf_counter() - start
    if results != expected:
        message = "cache results differ from independently accumulated fixture values"
        raise AssertionError(message)
    return {
        "pid": os.getpid(),
        "producer_calls": calls,
        "wall_seconds": elapsed,
        "result_sha256": _digest(results),
    }


def _run_worker(directory: Path) -> dict[str, Any]:
    """A fresh bounded process with the current interpreter and product source."""
    result = subprocess.run(  # noqa: S603 — owned interpreter and literal module
        [
            sys.executable,
            "-m",
            "bench.profiles.cache_reuse",
            "--worker-directory",
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return cast("dict[str, Any]", json.loads(result.stdout))


def main() -> None:
    """Write the cross-process reuse proof and host-specific wall measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--batches", type=int, default=64)
    parser.add_argument("--rows", type=int, default=2048)
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_directory is not None:
        print(json.dumps(_worker(args.worker_directory)))
        return
    if args.batches <= 0 or args.rows <= 0:
        parser.error("batches and rows must be positive")
    with TemporaryDirectory(prefix="unstd-cache-proof-") as temporary:
        directory = Path(temporary)
        fixture = _prepare(directory, args.batches, args.rows)
        cold = _run_worker(directory)
        warm = _run_worker(directory)
        assert cold["pid"] != warm["pid"], "proof must use distinct processes"
        assert cold["producer_calls"] == args.batches, "cold run skipped work"
        assert warm["producer_calls"] == 0, "warm run repeated producer work"
        assert cold["result_sha256"] == warm["result_sha256"], "results differ"
        report = {
            "schema": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "workload": "synthetic compressed eval export summaries",
            "command": (
                "python -m bench.profiles.cache_reuse "
                f"--batches {args.batches} --rows {args.rows} --output PATH"
            ),
            "environment": {
                "python": sys.version,
                "platform": platform.platform(),
                "machine": platform.machine(),
                "sqlite": sqlite3.sqlite_version,
            },
            "source_sha256": {
                "cache": sha256(Path(cache_module.__file__).read_bytes()).hexdigest(),
                "proof": sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            "fixture": fixture,
            "cold": cold,
            "warm": warm,
            "warm_speedup": cold["wall_seconds"] / warm["wall_seconds"],
            "database_bytes": (directory / "results.sqlite3").stat().st_size,
            "verified": {
                "independent_expected_results": True,
                "distinct_processes": True,
                "warm_producer_calls_zero": True,
                "same_output_digest": True,
            },
            "limits": (
                "One host and synthetic workload; fixture generation and process "
                "startup are outside the timer. Cache initialization, lookup, "
                "decoding, cold parsing, aggregation and commits are timed. "
                "A warm run skips source reads; include all producer inputs in "
                "real cache keys. Timing is diagnostic, not a portable speed guarantee."
            ),
        }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
