"""Read-only paired text-backend measurements; no production dispatch changes."""

from __future__ import annotations

import argparse
from functools import partial
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import statistics
import struct
import sys
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


# Direct execution does not put the repository's benchmark package on sys.path.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench import speeds  # noqa: E402
from bench.timing import _calibrate  # noqa: E402
from unstd.text import fuzz  # noqa: E402


def batch(fn: Callable[[], object], iterations: int) -> dict[str, float]:
    """Retain both clocks around the same unadjusted loop."""
    cpu = time.thread_time_ns()
    wall = time.perf_counter_ns()
    for _ in range(iterations):
        fn()
    wall = time.perf_counter_ns() - wall
    cpu = time.thread_time_ns() - cpu
    return {"wall_ns": wall / iterations, "thread_cpu_ns": cpu / iterations}


def identical(a: object, b: object) -> bool:
    """Compare nested results, retaining exact binary64 scores and types."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and isinstance(b, float):
        return struct.pack("!d", a) == struct.pack("!d", b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            identical(x, y) for x, y in zip(a, b, strict=True)
        )
    return bool(a == b)


def paired(
    name: str,
    baseline: Callable[[], object],
    candidate: Callable[[], object],
    *,
    note: str,
) -> dict[str, Any]:
    """Verify outputs outside timing and alternate order over seven pairs."""
    if not identical(baseline(), candidate()):
        msg = f"incompatible output: {name}"
        raise ValueError(msg)
    iterations = max(_calibrate(baseline)[0], _calibrate(candidate)[0])
    samples = []
    for repetition in range(7):
        order = ("baseline", "candidate")
        if repetition % 2:
            order = ("candidate", "baseline")
        functions = {"baseline": baseline, "candidate": candidate}
        sample: dict[str, Any] = {"order": order}
        for side in order:
            sample[side] = batch(functions[side], iterations)
        samples.append(sample)
    if not identical(baseline(), candidate()):
        msg = f"output drift after timing: {name}"
        raise ValueError(msg)
    medians = {
        side: statistics.median(sample[side]["wall_ns"] for sample in samples)
        for side in ("baseline", "candidate")
    }
    spreads = {}
    for side in ("baseline", "candidate"):
        values = [sample[side]["wall_ns"] for sample in samples]
        spreads[side] = max(values) / min(values) - 1
    return {
        "name": name,
        "note": note,
        "iterations": iterations,
        "outputs_bit_identical_before_and_after": True,
        "samples": samples,
        "median_ns": medians,
        "median_paired_candidate_over_baseline": statistics.median(
            sample["candidate"]["wall_ns"] / sample["baseline"]["wall_ns"]
            for sample in samples
        ),
        "spread": spreads,
        "noisy": any(value > 0.25 for value in spreads.values()),
    }


def cases() -> list[tuple[str, Callable[[], object], Callable[[], object], str]]:
    """Compare installed scorers and opt-in worker counts at several sizes."""
    result: list[tuple[str, Callable[[], object], Callable[[], object], str]] = []
    for length in (12, 64, 1024):
        a = ("café東京abc" * (length // 9 + 1))[:length]
        b = a[: length // 3] + "龍" + a[length // 3 + 1 : -1] + "Z"
        for name, portable, installed in (
            ("distance", fuzz._levenshtein, fuzz.distance),
            ("jaro_winkler", fuzz._jaro_winkler, fuzz.jaro_winkler),
            ("indel_ratio", fuzz._indel_ratio, fuzz.indel_ratio),
        ):
            result.append(
                (
                    f"{name}/{length}",
                    partial(portable, a, b),
                    partial(installed, a, b),
                    "baseline=base-install wrapper including available C; candidate=already installed RapidFuzz direct binding",
                )
            )
    for size in (1, 16, 128, 512):
        values = [f"contact-{i:05d}-café" for i in range(size)]
        result.append(
            (
                f"cdist-workers/{size}x{size}",
                partial(fuzz.cdist, values, values, workers=1),
                partial(fuzz.cdist, values, values, workers=2),
                "complete public float64 matrix/list conversion; only workers changes from 1 to 2",
            )
        )
    return result


def main() -> None:
    """Write raw paired evidence with exact source and runtime identities."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        rows = [paired(name, a, b, note=note) for name, a, b, note in cases()]
    finally:
        if was_enabled:
            gc.enable()
    sources = [Path(__file__), ROOT / "src/unstd/text/fuzz.py"]
    native = sys.modules.get("unstd.text._text_native")
    if native is not None and native.__file__ is not None:
        sources.append(Path(native.__file__))
    report = {
        "schema": 1,
        "environment": speeds.environment(),
        "python_full": sys.version,
        "platform": platform.platform(),
        "rapidfuzz_version": importlib.metadata.version("rapidfuzz"),
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "limitation": "shared host; thread CPU excludes work in other native worker threads; no zero-overhead guarantee",
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows)} checked comparisons; {sum(row['noisy'] for row in rows)} noisy")


if __name__ == "__main__":
    main()
