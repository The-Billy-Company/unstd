"""Paired cold-fill throughput against the frozen first cache implementation.

Workers perform real PBKDF2 derivations, with no simulated producer sleeps.
Subprocess readiness and measured producer intervals independently prove overlap.
Run ``python -m bench.profiles.cache_concurrency --output /tmp/cache-fills.json``.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import UTC, datetime
import gzip
from hashlib import pbkdf2_hmac, sha256
import importlib.util
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any, cast

import unstd.cache as cache_module
from unstd.cache import Cache, locking


_BASELINE = Path(__file__).resolve().parents[1] / "reports/cache/first-version.py.gz"
_FIRST_HASH = "de9162d4565f51fffd99115af8981d211ef099a8d2b4d449f4df37e94f213428"


def _cache_type(variant: str) -> type[Cache]:
    """Load the frozen source independently, retaining the candidate's imports."""
    if variant == "after":
        return Cache
    source = gzip.decompress(_BASELINE.read_bytes())
    assert sha256(source).hexdigest() == _FIRST_HASH, "baseline source changed"
    with TemporaryDirectory(prefix="unstd-cache-before-") as temporary:
        path = Path(temporary) / "before.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("_cache_first_version", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return cast("type[Cache]", module.Cache)


def _derive(key: str, iterations: int) -> dict[str, str]:
    """One genuine deterministic native CPU workload usable by concurrent workers."""
    return {
        "key": key,
        "digest": pbkdf2_hmac(
            "sha256", key.encode(), b"unstd-cache-concurrency-v1", iterations
        ).hex(),
    }


def _worker(
    directory: Path, variant: str, namespace: str, key: str, iterations: int
) -> None:
    cache = _cache_type(variant)(directory / "results.sqlite3", namespace=namespace)
    interval: list[int] = []
    calls = 0

    def produce() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        interval.append(time.monotonic_ns())
        result = _derive(key, iterations)
        interval.append(time.monotonic_ns())
        return result

    (directory / f"ready-{key}").write_text("ready", encoding="ascii")
    sys.stdin.read(1)
    result = cache.get_or_compute(key, produce)
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "calls": calls,
                "interval_ns": interval,
                "result": result,
            }
        ),
        flush=True,
    )


def _peak_overlap(results: list[dict[str, Any]]) -> int:
    events = [
        (stamp, delta)
        for result in results
        for stamp, delta in zip(result["interval_ns"], (1, -1), strict=True)
    ]
    active = peak = 0
    for _, delta in sorted(events):
        active += delta
        peak = max(peak, active)
    return peak


def _keys(namespace: str, workers: int) -> list[str]:
    """Distinct stripes isolate the independent-key scenario under measurement."""
    keys = []
    stripes = set()
    for index in range(10_000):
        key = f"job-{index}"
        stripe = locking._stripe(namespace, key)
        if stripe not in stripes:
            stripes.add(stripe)
            keys.append(key)
        if len(keys) == workers:
            return keys
    message = "unable to select distinct lock stripes"
    raise AssertionError(message)


def _round(
    root: Path, variant: str, namespace: str, keys: list[str], iterations: int
) -> dict[str, Any]:
    directory = root / f"{variant}-{namespace}"
    directory.mkdir()
    _cache_type(variant)(directory / "results.sqlite3", namespace=namespace)
    expected = {key: _derive(key, iterations) for key in keys}
    with ExitStack() as stack:

        def start_worker(key: str) -> subprocess.Popen[str]:
            worker = stack.enter_context(
                subprocess.Popen(  # noqa: S603 — explicit interpreter and owned module
                    [
                        sys.executable,
                        "-m",
                        "bench.profiles.cache_concurrency",
                        "--worker-directory",
                        str(directory),
                        "--variant",
                        variant,
                        "--namespace",
                        namespace,
                        "--key",
                        key,
                        "--iterations",
                        str(iterations),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
            # Register immediately so a later spawn failure cannot strand an
            # earlier child waiting at its start barrier during stack cleanup.
            stack.callback(_stop_worker, worker)
            return worker

        workers = [start_worker(key) for key in keys]
        try:
            deadline = time.monotonic() + 30
            while not all((directory / f"ready-{key}").exists() for key in keys):
                if time.monotonic() >= deadline or any(
                    worker.poll() is not None for worker in workers
                ):
                    message = "cache workers failed to reach the start barrier"
                    raise RuntimeError(message)
                # Coordination only; this wait is outside the measured region.
                time.sleep(0.005)
            start = time.perf_counter()
            for worker in workers:
                assert worker.stdin is not None
                worker.stdin.write("g")
                worker.stdin.flush()
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=30)
                assert worker.returncode == 0, stderr
                results.append(json.loads(stdout))
            elapsed = time.perf_counter() - start
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.communicate(timeout=5)
    assert len({result["pid"] for result in results}) == len(keys)
    assert all(result["calls"] == 1 for result in results)
    assert {result["result"]["key"]: result["result"] for result in results} == expected
    reader = _cache_type(variant)(directory / "results.sqlite3", namespace=namespace)
    assert {key: reader.get(key) for key in keys} == expected
    return {
        "wall_seconds": elapsed,
        "peak_producers": _peak_overlap(results),
        "workers": results,
    }


def _stop_worker(worker: subprocess.Popen[str]) -> None:
    """Reap an owned worker on startup, measurement, or cleanup failure."""
    if worker.poll() is None:
        worker.kill()
        worker.communicate(timeout=5)


def main() -> None:
    """Capture paired throughput, raw intervals, output checks, and source identities."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=300_000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--variant", choices=("before", "after"), help=argparse.SUPPRESS
    )
    parser.add_argument("--namespace", help=argparse.SUPPRESS)
    parser.add_argument("--key", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_directory is not None:
        _worker(
            args.worker_directory,
            args.variant,
            args.namespace,
            args.key,
            args.iterations,
        )
        return
    if not 2 <= args.workers <= 64 or args.rounds < 1 or args.iterations < 1:
        parser.error("workers must be 2..64; rounds and iterations must be positive")
    measurements: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    with TemporaryDirectory(prefix="unstd-cache-parallel-") as temporary:
        for index in range(args.rounds):
            namespace = f"round-{index}"
            keys = _keys(namespace, args.workers)
            order = ("before", "after") if index % 2 == 0 else ("after", "before")
            for variant in order:
                measurements[variant].append(
                    _round(Path(temporary), variant, namespace, keys, args.iterations)
                )
    before = median(row["wall_seconds"] for row in measurements["before"])
    after = median(row["wall_seconds"] for row in measurements["after"])
    assert all(row["peak_producers"] == 1 for row in measurements["before"])
    assert all(row["peak_producers"] > 1 for row in measurements["after"])
    report = {
        "schema": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "command": (
            "python -m bench.profiles.cache_concurrency "
            f"--workers {args.workers} --rounds {args.rounds} "
            f"--iterations {args.iterations} --output PATH"
        ),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "source_sha256": {
            "before": _FIRST_HASH,
            "after": sha256(Path(cache_module.__file__).read_bytes()).hexdigest(),
            "locking": sha256(Path(locking.__file__).read_bytes()).hexdigest(),
            "proof": sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "workers": args.workers,
        "iterations_per_producer": args.iterations,
        "before_median_seconds": before,
        "after_median_seconds": after,
        "median_speedup": before / after,
        "measurements": measurements,
        "verified": {
            "exact_outputs": True,
            "persisted_values": True,
            "producer_overlap": True,
        },
        "limits": (
            "Synthetic native CPU workload on one host, with deliberately distinct "
            "lock stripes. Worker startup and reference computations excluded. "
            "Timed region includes dispatch, cache fills, publication and worker "
            "exit. Same-stripe work serializes; measured gains depend on available CPUs."
        ),
    }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
