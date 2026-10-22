"""Output-checked, paired timings of saved and current crypto hashing sources.

Run ``python bench/crypto_hash.py --compare-source /path/to/before/src
--output /tmp/crypto-hash.json``. The saved source directory contains
``unstd/crypto/digest.py`` and ``unstd/crypto/token.py``. Inputs are prepared
outside timing; both implementations hash the same bytes. Batch order alternates,
and the JSON report retains every sample rather than asserting a speed floor.
Minimum and median batch costs are reported separately; cases with a sample
spread over 25% are marked noisy. These measurements describe the recorded host.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import partial
import gc
import hashlib
from importlib.metadata import version
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


_KEY = bytes(range(32))
_CONTEXT = "unstd hashing benchmark v1"
_MAX_ITERATIONS = 1 << 24


@dataclass(frozen=True, slots=True)
class Workload:
    """A pair of operations over one input prepared before timing."""

    name: str
    input_bytes: int
    before: Callable[[], bytes | str]
    after: Callable[[], bytes | str]


def _load(source: Path, module: str) -> Any:
    path = source / "unstd" / "crypto" / f"{module}.py"
    name = f"_crypto_hash_before_{module}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"cannot load saved source: {path}"
        raise RuntimeError(msg)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def _signatures(digest: Any, token: Any) -> dict[str, str]:
    signatures = {
        f"{prefix}.{name}": str(inspect.signature(getattr(module, name)))
        for prefix, module in (("digest", digest), ("token", token))
        for name in module.__all__
        if callable(getattr(module, name))
    }
    for name in ("__init__", "update", "raw", "hex"):
        signatures[f"digest.Stream.{name}"] = str(
            inspect.signature(getattr(digest.Stream, name))
        )
    return signatures


def _source_hashes(source: Path) -> dict[str, str]:
    return {
        str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((source / "unstd" / "crypto").rglob("*"))
        if path.is_file() and path.suffix in {".py", ".pyi", ".c", ".h", ".so", ".pyd"}
    }


def _stream(stream_type: Any, chunks: tuple[bytes, ...]) -> bytes:
    return stream_type(chunks).raw()  # type: ignore[no-any-return]


def _workloads(before: tuple[Any, Any], after: tuple[Any, Any]) -> list[Workload]:
    blobs = {
        size: (bytes(range(256)) * ((size + 255) // 256))[:size]
        for size in (64, 1 << 20, 8 << 20, 16 << 20, 64 << 20)
    }
    cases = []
    matrix = (
        ("raw", (64, 1 << 20, 8 << 20, 16 << 20, 64 << 20)),
        ("hex", (64, 1 << 20, 8 << 20, 64 << 20)),
        ("hex8", (64, 8 << 20)),
        ("derive_key", (64, 1 << 20, 64 << 20)),
        ("mac_raw", (64, 1 << 20, 64 << 20)),
        ("mac_hex", (64, 8 << 20, 64 << 20)),
    )
    for operation, sizes in matrix:
        for size in sizes:
            calls = []
            for digest, token in (before, after):
                data = blobs[size]
                if operation == "hex8":
                    call = partial(digest.hex, data, 8)
                elif operation == "derive_key":
                    call = partial(digest.derive_key, _CONTEXT, data)
                elif operation.startswith("mac_"):
                    call = partial(getattr(token, operation), _KEY, data)
                else:
                    call = partial(getattr(digest, operation), data)
                calls.append(call)
            label = "64 B" if size == 64 else f"{size >> 20} MiB"
            cases.append(Workload(f"{operation} / {label}", size, *calls))
    for size, chunk_size in ((1 << 20, 1 << 10), (64 << 20, 8 << 20)):
        data = blobs[size]
        chunks = tuple(data[at : at + chunk_size] for at in range(0, size, chunk_size))
        calls = [
            partial(_stream, modules[0].Stream, chunks) for modules in (before, after)
        ]
        cases.append(
            Workload(
                f"Stream / {size >> 20} MiB / {chunk_size >> 10} KiB chunks",
                size,
                *calls,
            )
        )
    return cases


def _batch(call: Callable[[], bytes | str], iterations: int) -> int:
    start = time.perf_counter_ns()
    for _ in range(iterations):
        call()
    return time.perf_counter_ns() - start


def _calibrate(call: Callable[[], bytes | str], target_ns: int) -> int:
    iterations = 1
    while True:
        elapsed = _batch(call, iterations)
        if elapsed >= target_ns // 2 or iterations >= _MAX_ITERATIONS:
            return max(
                1,
                min(_MAX_ITERATIONS, round(iterations * target_ns / max(elapsed, 1))),
            )
        scale = max(2, min(32, target_ns // max(elapsed, 1)))
        iterations = min(_MAX_ITERATIONS, iterations * scale)


def _measure(
    case: Workload, rounds: int, seconds: float, case_index: int
) -> dict[str, Any]:
    expected = case.before()
    actual = case.after()
    if type(actual) is not type(expected) or actual != expected:
        msg = f"outputs differ: {case.name}"
        raise AssertionError(msg)
    calls = {"before": case.before, "after": case.after}
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        iterations = {
            label: _calibrate(call, round(seconds * 1e9))
            for label, call in calls.items()
        }
        samples: dict[str, list[dict[str, int | float]]] = {
            label: [] for label in calls
        }
        order = []
        for round_index in range(rounds):
            labels = list(calls)[:: 1 if (case_index + round_index) % 2 == 0 else -1]
            order.append(labels)
            for label in labels:
                elapsed = _batch(calls[label], iterations[label])
                samples[label].append(
                    {
                        "iterations": iterations[label],
                        "elapsed_ns": elapsed,
                        "per_op_ns": elapsed / iterations[label],
                    }
                )
    finally:
        if gc_was_enabled:
            gc.enable()
    medians = {
        label: statistics.median(row["per_op_ns"] for row in rows)
        for label, rows in samples.items()
    }
    minimums = {
        label: min(row["per_op_ns"] for row in rows) for label, rows in samples.items()
    }
    spreads = {
        label: (max(row["per_op_ns"] for row in rows) / minimums[label]) - 1
        for label, rows in samples.items()
    }
    paired_ratios = [
        left["per_op_ns"] / right["per_op_ns"]
        for left, right in zip(samples["before"], samples["after"], strict=True)
    ]
    return {
        "input_bytes": case.input_bytes,
        "output_sha256": hashlib.sha256(
            expected if isinstance(expected, bytes) else expected.encode("ascii")
        ).hexdigest(),
        "order": order,
        "samples": samples,
        "median_ns": medians,
        "minimum_ns": minimums,
        "minimum_speedup": minimums["before"] / minimums["after"],
        "median_paired_speedup": statistics.median(paired_ratios),
        "paired_speedups": paired_ratios,
        "spread": spreads,
        "noisy": any(spread > 0.25 for spread in spreads.values()),
    }


def _load_average() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except (AttributeError, OSError):
        return None


def main() -> None:
    """Validate API signatures and outputs, then record alternating batch samples."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--seconds", type=float, default=0.03)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if not math.isfinite(args.seconds) or not 0.001 <= args.seconds <= 1:
        parser.error("--seconds must be finite and between 0.001 and 1")
    from unstd.crypto import digest, token

    source = Path(digest.__file__).resolve().parents[2]
    before_source = args.compare_source.resolve()
    before = (_load(before_source, "digest"), _load(before_source, "token"))
    after = (digest, token)
    signatures = {"before": _signatures(*before), "after": _signatures(*after)}
    if signatures["before"] != signatures["after"]:
        msg = "public signatures changed"
        raise AssertionError(msg)
    for old, new in zip(before, after, strict=True):
        if old.__all__ != new.__all__ or any(
            getattr(old, name) != getattr(new, name)
            for name in old.__all__
            if not callable(getattr(old, name))
        ):
            msg = "public exports or constants changed"
            raise AssertionError(msg)
    hashes = {
        "before": _source_hashes(before_source),
        "after": _source_hashes(source),
    }
    cases = {}
    for index, case in enumerate(_workloads(before, after)):
        row = _measure(case, args.rounds, args.seconds, index)
        cases[case.name] = row
        print(
            f"{case.name:45} {row['median_paired_speedup']:6.2f}x "
            f"{row['median_ns']['before'] / 1000:10.2f} -> "
            f"{row['median_ns']['after'] / 1000:10.2f} us"
            f"{'  noisy' if row['noisy'] else ''}",
            flush=True,
        )
    if hashes != {
        "before": _source_hashes(before_source),
        "after": _source_hashes(source),
    }:
        msg = "crypto source changed during measurement"
        raise AssertionError(msg)
    report = {
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "load_average": _load_average(),
            "blake3_version": version("blake3"),
            "rayon_num_threads": os.environ.get("RAYON_NUM_THREADS"),
        },
        "rounds": args.rounds,
        "target_batch_seconds": args.seconds,
        "estimators": {
            "minimum_speedup": "ratio of the fastest before and after batch means",
            "median_paired_speedup": "median of per-round before/after ratios",
            "noisy": "any per-implementation sample spread exceeds 25%",
        },
        "before_source": str(before_source),
        "after_source": str(source),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": hashes,
        "signatures": signatures,
        "cases": cases,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
