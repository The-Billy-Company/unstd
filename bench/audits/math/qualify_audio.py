"""Paired complete audio calls against an explicit pre-change source snapshot."""

from __future__ import annotations

import argparse
from functools import partial
import gc
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import TYPE_CHECKING, Any
import wave

import numpy as np


if TYPE_CHECKING:
    from collections.abc import Callable


ROOT = Path(__file__).resolve().parents[3]


def load(path: Path, name: str) -> Any:
    """Load independent wrapper globals without changing the installed module."""
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path))
    )
    if spec is None or spec.loader is None:
        raise ValueError(str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def payload(width: int, count: int) -> bytes:
    """A mono WAV containing deterministic rails and interior sample counts."""
    code = {1: "u1", 2: "<i2", 4: "<i4"}[width]
    rails = {1: [0, 1, 127, 128, 255], 2: [-32768, -1, 0, 1, 32767]}
    values = rails.get(width, [-(2**31), -65537, -1, 0, 65535, 2**31 - 1])
    raw = np.resize(np.array(values, dtype=code), count).tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(width)
        handle.setframerate(16000)
        handle.writeframes(raw)
    return output.getvalue()


def read(module: Any, data: bytes, dtype: str, *, blocks: bool = False) -> object:
    """Include stream creation, public parsing, decoding and complete consumption."""
    if blocks:
        return list(module.blocks(io.BytesIO(data), dtype=dtype, blocksize=16))
    return module.read(io.BytesIO(data), dtype=dtype)


def signature(value: Any) -> object:
    """Retain exact numeric bytes, dtype, shape and storage ownership."""
    if isinstance(value, np.ndarray):
        return (
            value.dtype.str,
            value.shape,
            value.tobytes(),
            value.flags.writeable,
            value.flags.owndata,
        )
    if isinstance(value, list | tuple):
        return type(value).__name__, tuple(signature(item) for item in value)
    return value


def batch(fn: Callable[[], object], count: int) -> dict[str, float]:
    """Record wall and current-thread CPU cost for the same unadjusted calls."""
    cpu = time.thread_time_ns()
    wall = time.perf_counter_ns()
    for _ in range(count):
        fn()
    wall = time.perf_counter_ns() - wall
    cpu = time.thread_time_ns() - cpu
    return {"wall_ns": wall / count, "cpu_ns": cpu / count}


def calibrate(fn: Callable[[], object]) -> int:
    """Bound calibration and aim for at least two milliseconds per batch."""
    count = 1
    while count < 8192:
        sample = batch(fn, count)
        if sample["wall_ns"] * count >= 2_000_000:
            return count
        count *= 2
    return count


def pair(
    name: str,
    baseline: Callable[[], object],
    candidate: Callable[[], object],
    *,
    result_signature: Callable[[Any], object] = signature,
) -> dict[str, Any]:
    """Check output parity before/after seven alternating adjacent pairs."""
    expected = result_signature(baseline())
    if result_signature(candidate()) != expected:
        raise ValueError(name)
    iterations = min(calibrate(baseline), calibrate(candidate))
    functions = {"baseline": baseline, "candidate": candidate}
    samples = []
    for turn in range(7):
        order = ("candidate", "baseline") if turn % 2 else ("baseline", "candidate")
        row: dict[str, Any] = {"order": order}
        for side in order:
            row[side] = batch(functions[side], iterations)
        samples.append(row)
    if (
        result_signature(baseline()) != expected
        or result_signature(candidate()) != expected
    ):
        raise ValueError(name)
    ratios = {
        clock: statistics.median(
            row["candidate"][clock] / row["baseline"][clock] for row in samples
        )
        for clock in ("wall_ns", "cpu_ns")
    }
    return {
        "name": name,
        "iterations": iterations,
        "samples": samples,
        "median_paired_candidate_over_baseline": ratios,
        "exact_result_checked_before_and_after": True,
    }


def main() -> None:
    """Qualify public SoundFile calls and every NumPy PCM width/dtype tier."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate_path = ROOT / "src/unstd/audio/wavx.py"
    baseline = load(args.baseline, "_audio_before")
    candidate = load(candidate_path, "_audio_after")
    cases: list[tuple[str, Callable[[], object], Callable[[], object]]] = []
    for route in ("soundfile", "numpy"):
        a, b = (
            (baseline, candidate)
            if route == "soundfile"
            else (
                load(args.baseline, "_audio_before_numpy"),
                load(candidate_path, "_audio_after_numpy"),
            )
        )
        if route == "numpy":
            a._HAVE_SOUNDFILE = b._HAVE_SOUNDFILE = False
        for width in (1, 2, 4):
            for count in (0, 1, 16, 1024, 65536):
                for dtype in ("float32", "float64", "int16"):
                    if route == "soundfile" and (width != 2 or dtype == "float64"):
                        continue
                    data = payload(width, count)
                    cases.append(
                        (
                            f"{route}/read/pcm{width * 8}/{count}/{dtype}",
                            partial(read, a, data, dtype),
                            partial(read, b, data, dtype),
                        )
                    )
        for count in (1, 65, 4096):
            data = payload(2, count)
            cases.extend(
                (
                    f"{route}/blocks/pcm16/{count}/{dtype}",
                    partial(read, a, data, dtype, blocks=True),
                    partial(read, b, data, dtype, blocks=True),
                )
                for dtype in ("float32", "int16")
            )
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        rows = [pair(name, a, b) for name, a, b in cases]
    finally:
        if was_enabled:
            gc.enable()
    report = {
        "schema": 1,
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "method": "seven alternating adjacent pairs; complete public calls; shared host; CPU=current thread",
        "sources": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), args.baseline, candidate_path)
        },
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(rows)} exact checked public-call comparisons")


if __name__ == "__main__":
    main()
