"""Checked PCM wrapping and normalization ports of Billy's audio consumers.

Run with ``--baseline /path/to/saved/wavx.py --output /path/to/report.json``.
``--billy-root ../billy`` adds hashes and source locations for the production
consumers. Fixtures are deterministic PCM16 bytes; no provider or network time is
included. Every timed call allocates its complete output, and allocation peaks
are measured separately from wall and current-thread CPU time.
"""

from __future__ import annotations

import argparse
import ast
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
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable

    from numpy.typing import NDArray


_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE = _ROOT / "src/unstd/audio/wavx.py"
_CONSUMERS = {
    "services/ai/core/domain/media/sound/audio.py": ("_sound", "synthesize"),
    "services/ai/transport/speech/elevenlabs/stt.py": ("_wav",),
    "services/ai/transport/livekit/sidecar/features/sidecar_features.py": (
        "resample_s16",
    ),
    "services/ai/transport/livekit/sidecar/core/runner.py": ("_ingest_pcm",),
    "services/ai/transport/livekit/worker/pcm_buffer.py": ("append",),
    "services/ai/transport/livekit/worker/diarized_stt.py": ("_recognize_impl",),
    "services/ai/transport/livekit/worker/smart_turn_model.py": ("predict_pcm16",),
}


class _Codec(Protocol):
    """Only the two byte operations exercised by the consumer ports."""

    def wrap_pcm(
        self, raw: Buffer, samplerate: int, *, channels: int = 1, width: int = 2
    ) -> bytes: ...

    def decode_pcm(
        self,
        raw: Buffer,
        *,
        channels: int = 1,
        width: int = 2,
        dtype: str = "float32",
    ) -> object: ...


@dataclass(frozen=True)
class Case:
    """One complete byte operation and its unchanged production reference."""

    name: str
    raw: bytes
    baseline: Callable[[], object]
    candidate: Callable[[], object]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load(path: Path, name: str) -> _Codec:
    """Compile explicit source bytes, including saved .txt files, without pyc reuse."""
    module = ModuleType(name)
    module.__file__ = str(path)
    # Both paths are trusted, explicit benchmark inputs; execute their current
    # source bytes rather than an import cache that could predate the measurement.
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)  # noqa: S102
    return cast("_Codec", module)


def _pcm(frames: int) -> bytes:
    """Deterministic PCM16 including both rails, zero and interior counts."""
    rails = np.array([-32768, -32767, -16385, -1, 0, 1, 16383, 32767], dtype="<i2")
    return np.resize(rails, frames).tobytes()


def _divide(raw: bytes) -> NDArray[np.float32]:
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _multiply(raw: bytes) -> NDArray[np.float32]:
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) * (1.0 / 32768.0)


def _wrap_copy(codec: _Codec, raw: bytearray) -> bytes:
    return codec.wrap_pcm(bytes(raw), 16000)


def _cases(baseline: _Codec, candidate: _Codec) -> list[Case]:
    cases: list[Case] = []
    for size in (640, 32000, 12 * 1024 * 1024):
        raw = _pcm(size // 2)
        mutable = bytearray(raw)
        cases.extend(
            (
                Case(
                    f"wrap/bytes/{size}",
                    raw,
                    partial(baseline.wrap_pcm, raw, 16000),
                    partial(candidate.wrap_pcm, raw, 16000),
                ),
                Case(
                    f"wrap/narration-bytearray/{size}",
                    raw,
                    partial(_wrap_copy, baseline, mutable),
                    partial(candidate.wrap_pcm, mutable, 16000),
                ),
            )
        )
    for frames in (320, 1600, 16000, 512000):
        raw = _pcm(frames)
        cases.extend(
            Case(
                f"decode/{name}/{frames}-frames",
                raw,
                partial(reference, raw),
                partial(candidate.decode_pcm, raw),
            )
            for name, reference in (("divide", _divide), ("multiply", _multiply))
        )
    return cases


def _signature(value: object) -> tuple[object, ...]:
    """Compare exact bytes and array dtype, shape, ownership and writability."""
    if type(value) is bytes:
        return (bytes, value)
    if isinstance(value, np.ndarray):
        if not value.flags.owndata or not value.flags.writeable:
            message = "decoded PCM must own writable storage"
            raise AssertionError(message)
        return (np.ndarray, value.dtype.str, value.shape, value.tobytes())
    message = f"unexpected audio output: {type(value).__name__}"
    raise AssertionError(message)


def _check(case: Case, expected: tuple[object, ...]) -> None:
    for side, function in (("baseline", case.baseline), ("candidate", case.candidate)):
        if _signature(function()) != expected:
            message = f"{case.name}: {side} output changed"
            raise AssertionError(message)


def _batch(function: Callable[[], object], iterations: int) -> dict[str, float]:
    cpu = time.thread_time_ns()
    wall = time.perf_counter_ns()
    for _ in range(iterations):
        function()
    elapsed_wall = time.perf_counter_ns() - wall
    elapsed_cpu = time.thread_time_ns() - cpu
    return {
        "wall_ns": elapsed_wall / iterations,
        "thread_cpu_ns": elapsed_cpu / iterations,
    }


def _calibrate(function: Callable[[], object]) -> int:
    iterations = 1
    while iterations < 16384:
        elapsed = _batch(function, iterations)["wall_ns"] * iterations
        if elapsed >= 2_000_000:
            return iterations
        iterations *= 2
    return iterations


def _peak(function: Callable[[], object]) -> int:
    """One complete retained result, outside all timed batches."""
    tracemalloc.start()
    try:
        result = function()
        peak = tracemalloc.get_traced_memory()[1]
        del result
        return peak
    finally:
        tracemalloc.stop()


def _pair(case: Case, rounds: int) -> dict[str, Any]:
    expected = _signature(case.baseline())
    _check(case, expected)
    iterations = min(_calibrate(case.baseline), _calibrate(case.candidate))
    functions = {"baseline": case.baseline, "candidate": case.candidate}
    samples = []
    for repetition in range(rounds):
        order = ("baseline", "candidate")
        if repetition % 2:
            order = order[::-1]
        row: dict[str, Any] = {"order": order}
        for side in order:
            row[side] = _batch(functions[side], iterations)
        samples.append(row)
    peaks = {side: _peak(function) for side, function in functions.items()}
    _check(case, expected)
    summary = {
        side: {
            clock: statistics.median(row[side][clock] for row in samples)
            for clock in ("wall_ns", "thread_cpu_ns")
        }
        for side in functions
    }
    for values in summary.values():
        values["input_mib_per_s"] = len(case.raw) * 1e9 / values["wall_ns"] / 2**20
    return {
        "name": case.name,
        "input_bytes": len(case.raw),
        "input_sha256": _sha(case.raw),
        "output_sha256": _sha(cast("bytes", expected[-1])),
        "output_dtype": expected[1] if expected[0] is np.ndarray else None,
        "output_shape": expected[2] if expected[0] is np.ndarray else None,
        "iterations": iterations,
        "samples": samples,
        "median": summary,
        "median_paired_candidate_over_baseline": {
            clock: statistics.median(
                row["candidate"][clock] / row["baseline"][clock] for row in samples
            )
            for clock in ("wall_ns", "thread_cpu_ns")
        },
        "peak_traced_bytes": peaks,
        "exact_result_checked_before_and_after": True,
    }


def _consumers(root: Path) -> dict[str, object]:
    contracts: dict[str, object] = {}
    for relative, names in _CONSUMERS.items():
        path = root / relative
        raw = path.read_bytes()
        source = raw.decode()
        nodes = ast.walk(ast.parse(source))
        for node in nodes:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name not in names:
                    continue
                body = ast.get_source_segment(source, node)
                assert body is not None
                key = f"{relative}:{node.name}"
                if key in contracts:
                    message = f"ambiguous Billy audio consumer: {key}"
                    raise ValueError(message)
                contracts[key] = {
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "file_sha256": _sha(raw),
                    "symbol_sha256": _sha(body.encode()),
                    "source": body,
                }
        if any(f"{relative}:{name}" not in contracts for name in names):
            message = f"missing Billy audio consumer: {relative}"
            raise ValueError(message)
    return contracts


def _report(
    baseline_path: Path, billy_root: Path | None, rounds: int
) -> dict[str, Any]:
    sources = [Path(__file__).resolve(), baseline_path, _CANDIDATE]
    sources.extend(sorted((_CANDIDATE.parent / "_internal").glob("*.py")))
    hashes = {str(path.resolve()): _sha(path.read_bytes()) for path in sources}
    baseline = _load(baseline_path, "_billy_audio_baseline")
    candidate = _load(_CANDIDATE, "_billy_audio_candidate")
    contracts = _consumers(billy_root) if billy_root is not None else {}
    cases = _cases(baseline, candidate)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        rows = [_pair(case, rounds) for case in cases]
    finally:
        if was_enabled:
            gc.enable()
    for path in sources:
        if _sha(path.read_bytes()) != hashes[str(path.resolve())]:
            message = f"audio source changed during measurement: {path}"
            raise RuntimeError(message)
    return {
        "schema": 1,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "method": {
            "rounds": rounds,
            "order": "alternating adjacent pairs on a shared host",
            "timed": "complete output allocation; wall and current-thread CPU",
            "memory": "one retained output under tracemalloc, outside timed calls",
            "validation": "exact bytes, dtype, shape, owned writable output, before/after",
            "excluded": "fixture construction, imports, async scheduling, provider/network time",
            "decode_reference": "Billy's frombuffer(int16).astype(float32) then divide or multiply",
        },
        "source_sha256": hashes,
        "billy_root": str(billy_root.resolve()) if billy_root is not None else None,
        "source_contracts": contracts,
        "rows": rows,
    }


def main() -> None:
    """Measure saved wrapping and production normalization against current APIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--billy-root", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    if args.rounds < 2:
        parser.error("rounds must be at least 2")
    report = _report(args.baseline.resolve(), args.billy_root, args.rounds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(report['rows'])} exact-checked paired audio comparisons")


if __name__ == "__main__":
    main()
