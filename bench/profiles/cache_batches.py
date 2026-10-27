"""Compare persisted cache batches with the frozen first-version cache.

Run ``python -m bench.profiles.cache_batches --output /tmp/cache-batches.json``.
The baseline source is authenticated before importing it into a separate module.
Timing alternates before/after order, and complete output/state checks run
outside every measured region. No sleeps simulate application work.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import gzip
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import platform
import sqlite3
from statistics import median
import sys
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any

import unstd.cache as cache_module
from unstd.cache import Cache
import unstd.cache.locking as cache_locking
from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable

    from unstd.serde.jsonx import Json


_FIRST_VERSION_SHA256 = (
    "de9162d4565f51fffd99115af8981d211ef099a8d2b4d449f4df37e94f213428"
)
_BASELINE = Path(__file__).resolve().parents[1] / "reports/cache/first-version.py.gz"


def _load_baseline(source: bytes, directory: Path) -> Any:
    """An authenticated historical class, with its own module globals."""
    path = directory / "first_version.py"
    path.write_bytes(source)
    specification = importlib.util.spec_from_file_location(
        "_cache_batches_first_version", path
    )
    if specification is None or specification.loader is None:
        message = "cannot create the first-version module loader"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.Cache


def _checked_samples(
    before: Callable[[], object],
    after: Callable[[], object],
    check: Callable[[object, object], None],
    rounds: int,
) -> dict[str, Any]:
    """Paired wall samples with order alternation and untimed state oracles."""
    before_samples: list[int] = []
    after_samples: list[int] = []
    for index in range(rounds):
        pair = (
            (("before", before, before_samples), ("after", after, after_samples))
            if index % 2 == 0
            else (("after", after, after_samples), ("before", before, before_samples))
        )
        observed = {}
        for name, operation, samples in pair:
            start = time.perf_counter_ns()
            result = operation()
            samples.append(time.perf_counter_ns() - start)
            observed[name] = result
        check(observed["before"], observed["after"])
    before_median = median(before_samples)
    after_median = median(after_samples)
    return {
        "first_version": {"median_ns": before_median, "samples_ns": before_samples},
        "current_batch": {"median_ns": after_median, "samples_ns": after_samples},
        "speedup": before_median / after_median,
    }


def _run(directory: Path, source: bytes, entries: int, rounds: int) -> dict[str, Any]:
    """Read and write the same synthetic summaries through both real APIs."""
    before_type = _load_baseline(source, directory)
    before = before_type(directory / "first.sqlite3", namespace="eval-summary-v1")
    after = Cache(directory / "batch.sqlite3", namespace="eval-summary-v1")
    values: dict[str, Json] = {
        f"completed-eval-{index:04d}": {
            "id": index,
            "tool_exact": (index % 11) / 10,
            "schema_valid": True,
            "counts": list(range(32)),
        }
        for index in range(entries)
    }
    expected = json.loads(json.dumps(values))
    for key, value in values.items():
        before.set(key, value)
    after.set_many(values)

    def individual_reads() -> dict[str, Json]:
        return {key: before.get(key) for key in values}

    def batch_reads() -> dict[str, Json]:
        return after.get_many(values)

    def individual_writes() -> None:
        for key, value in values.items():
            before.set(key, value)

    def batch_writes() -> None:
        after.set_many(values)

    def check() -> None:
        assert individual_reads() == expected, "first-version persisted values differ"
        assert batch_reads() == expected, "batch persisted values differ"

    def check_reads(before_result: object, after_result: object) -> None:
        assert before_result == expected, "timed first-version read differs"
        assert after_result == expected, "timed batch read differs"
        check()

    def check_writes(before_result: object, after_result: object) -> None:
        assert before_result is None
        assert after_result is None
        check()

    check()
    reads = _checked_samples(individual_reads, batch_reads, check_reads, rounds)
    writes = _checked_samples(individual_writes, batch_writes, check_writes, rounds)
    return {
        "fixture": {
            "entries": entries,
            "value": "synthetic completed-evaluation summary with 32 integer counts",
            "json_bytes": len(json.dumps(values, sort_keys=True).encode()),
            "expected_sha256": sha256(
                json.dumps(expected, sort_keys=True).encode()
            ).hexdigest(),
        },
        "reads": reads,
        "writes": writes,
        "verified": {
            "independent_expected_values": True,
            "persisted_state_after_every_pair": True,
            "separate_baseline_module_globals": True,
            "authenticated_first_version_source": True,
        },
    }


def main() -> None:
    """Capture reproducible first-version versus batch operation evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=64)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--baseline", type=Path, default=_BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.entries <= 0 or args.rounds <= 0:
        parser.error("entries and rounds must be positive")
    compressed = args.baseline.read_bytes()
    source = gzip.decompress(compressed)
    if sha256(source).hexdigest() != _FIRST_VERSION_SHA256:
        parser.error("the first-version baseline does not match its frozen source hash")
    with TemporaryDirectory(prefix="unstd-cache-batches-") as temporary:
        results = _run(Path(temporary), source, args.entries, args.rounds)
    report = {
        "schema": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "command": (
            "python -m bench.profiles.cache_batches "
            f"--entries {args.entries} --rounds {args.rounds} --output PATH"
        ),
        "rounds": args.rounds,
        "estimator": "median of paired wall samples; alternating execution order",
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "sqlite": sqlite3.sqlite_version,
        },
        "source_sha256": {
            "first_version": _FIRST_VERSION_SHA256,
            "first_version_gzip": sha256(compressed).hexdigest(),
            "candidate": sha256(Path(cache_module.__file__).read_bytes()).hexdigest(),
            "candidate_locking": sha256(
                Path(cache_locking.__file__).read_bytes()
            ).hexdigest(),
            "shared_jsonx": sha256(Path(jsonx.__file__).read_bytes()).hexdigest(),
            "proof": sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        **results,
        "limits": (
            "One host and synthetic values; constructors, fixture setup, and state "
            "checks are outside timing. Every read decodes all values. Every write "
            "encodes all values and commits with SQLite full synchronization. "
            "The first version uses one public get/set call per key; the candidate "
            "uses one get_many/set_many call per batch. Current serde/fs code is "
            "shared by both versions. Speedups are workload-specific."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, indent=2) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
