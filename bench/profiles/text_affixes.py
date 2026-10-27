"""Compare saved/current edit distance through native and source-only wrappers.

Run this file with explicit source roots and compiled text extensions. RapidFuzz
is excluded by the isolated loader so both base-install paths get measured.
Checks, calibration and allocation tracing stay outside paired timing batches.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.core.timing import _calibrate
from bench.profiles.text_native_compare import _bundle


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Case:
    """An exact edit count with an independent matrix or one-edit witness."""

    name: str
    a: str
    b: str
    expected: int
    proof: str


def _matrix(a: str, b: str) -> int:
    """Independent Wagner-Fischer rows, with no shared-end optimization."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row = row, [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[-1] + 1, previous[j - 1] + (ca != cb)))
    return row[-1]


def _cases() -> list[Case]:
    cases = []
    for name, a, b in (
        ("short-no-affixes", "kitten", "sitting"),
        ("unrelated-400", "abcd" * 100, "wxyz" * 100),
        ("empty", "", "abc"),
    ):
        cases.append(Case(name, a, b, _matrix(a, b), "full untrimmed edit matrix"))
    for name, prefix, left, right, suffix in (
        ("both-ends-8192", "a" * 4096, "cat", "cut", "b" * 4096),
        ("prefix-65536-insertion", "a" * 65536, "cat", "cats", ""),
        ("suffix-32768-insertion", "", "cat", "cats", "b" * 32768),
        ("mixed-unicode-kind-32768", "a" * 32768, "b", "😀", ""),
        (
            "diverse-unicode-600",
            "".join(chr(0x1000 + i) for i in range(300)),
            "x",
            "y",
            "".join(chr(0x1000 + i) for i in range(300)),
        ),
    ):
        # Unequal strings require at least one edit; replacing/inserting the
        # named core character constructs the other with exactly one edit.
        if _matrix(left, right) != 1:
            msg = f"invalid one-edit witness: {name}"
            raise AssertionError(msg)
        cases.append(
            Case(
                name,
                prefix + left + suffix,
                prefix + right + suffix,
                1,
                "unequal strings; explicit single substitution/insertion witness",
            )
        )
    return cases


def _receipt(path: Path) -> dict[str, str]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _route(wrapper: Any, case: Case) -> str:
    """Observe actual native acceptance and Python fallback outside timing."""
    native, portable = wrapper._native_distance, wrapper._string_distance
    events = []

    def recorded_native(a: str, b: str) -> Any:
        result = native(a, b)
        events.append("native-declined" if result is NotImplemented else "native")
        return result

    def recorded_portable(a: str, b: str) -> int:
        events.append("portable")
        result: int = portable(a, b)
        return result

    wrapper._native_distance = recorded_native if native is not None else None
    wrapper._string_distance = recorded_portable
    try:
        if wrapper.distance(case.a, case.b) != case.expected:
            msg = f"incorrect result during dispatch check: {case.name}"
            raise AssertionError(msg)
    finally:
        wrapper._native_distance, wrapper._string_distance = native, portable
    return ", ".join(events) or "empty/equality shortcut"


def _batch(call: Callable[[], int], count: int) -> dict[str, float]:
    cpu_start, start = time.process_time_ns(), time.perf_counter_ns()
    for _ in range(count):
        call()
    elapsed = time.perf_counter_ns() - start
    cpu_elapsed = time.process_time_ns() - cpu_start
    return {"wall_ns": elapsed / count, "cpu_ns": cpu_elapsed / count}


def main() -> None:
    """Save checked timings, observed dispatch and source/binary receipts."""
    parser = argparse.ArgumentParser(description=__doc__)
    for arm in ("baseline", "candidate"):
        parser.add_argument(f"--{arm}-source", type=Path, required=True)
        parser.add_argument(f"--{arm}-native", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("at least three paired rounds are required")
    sources = {
        arm: getattr(args, f"{arm}_source").resolve()
        for arm in ("baseline", "candidate")
    }
    binaries = {arm: getattr(args, f"{arm}_native").resolve() for arm in sources}
    receipts = {
        arm: {
            "wrapper": _receipt(source / "unstd/text/fuzz.py"),
            "native": _receipt(binaries[arm]),
        }
        for arm, source in sources.items()
    }
    report: dict[str, Any] = {
        "utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "harness": _receipt(Path(__file__)),
        "implementations": receipts,
        "method": "isolated base-install public wrappers; alternating paired batch means; median ratios; independent per-arm calibration; GC enabled; allocations measured separately",
        "limitation": "synthetic workload shapes on one host; RapidFuzz-present public binding is unchanged and excluded",
        "rows": {},
    }
    for lane in ("native", "portable"):
        wrappers = {
            arm: _bundle(binaries[arm], source, f"affixes_{lane}_{arm}")["wrapper"]
            for arm, source in sources.items()
        }
        if lane == "portable":
            for wrapper in wrappers.values():
                wrapper._native_distance = None
        for case in _cases():
            calls = {
                arm: partial(wrapper.distance, case.a, case.b)
                for arm, wrapper in wrappers.items()
            }
            routes = {arm: _route(wrapper, case) for arm, wrapper in wrappers.items()}
            counts = {arm: _calibrate(call)[0] for arm, call in calls.items()}
            samples: dict[str, list[dict[str, float]]] = {arm: [] for arm in calls}
            orders = []
            for index in range(args.rounds):
                order = list(calls)[:: 1 if index % 2 == 0 else -1]
                orders.append(order)
                for arm in order:
                    samples[arm].append(_batch(calls[arm], counts[arm]))
            peaks = {}
            for arm, call in calls.items():
                if call() != case.expected:
                    msg = f"incorrect timed result: {lane}/{case.name}/{arm}"
                    raise AssertionError(msg)
                gc.collect()
                tracemalloc.start()
                try:
                    call()
                    peaks[arm] = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
            medians = {
                arm: statistics.median(s["wall_ns"] for s in values)
                for arm, values in samples.items()
            }
            row = {
                "lengths": [len(case.a), len(case.b)],
                "expected": case.expected,
                "proof": case.proof,
                "routes": routes,
                "iterations": counts,
                "order": orders,
                "samples": samples,
                "median_wall_ns": medians,
                "paired_speedup": statistics.median(
                    b["wall_ns"] / a["wall_ns"]
                    for b, a in zip(
                        samples["baseline"], samples["candidate"], strict=True
                    )
                ),
                "peak_python_allocation_bytes": peaks,
            }
            report["rows"][f"{lane}/{case.name}"] = row
            print(
                f"{lane}/{case.name}: {medians['baseline'] / 1e6:.6f}ms -> {medians['candidate'] / 1e6:.6f}ms ({row['paired_speedup']:.2f}x)",
                flush=True,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    # An edited wrapper/library during the run invalidates every measured row.
    for arm, source in sources.items():
        if receipts[arm] != {
            "wrapper": _receipt(source / "unstd/text/fuzz.py"),
            "native": _receipt(binaries[arm]),
        }:
            msg = f"implementation changed during timing: {arm}"
            raise RuntimeError(msg)


if __name__ == "__main__":
    main()
