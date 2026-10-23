"""Compare complete public WAV calls using two explicit libsndfile binaries."""

from __future__ import annotations

import argparse
from functools import partial
import gc
import hashlib
import importlib
import io
import json
from pathlib import Path
import platform
import struct
import sys
import time
from typing import TYPE_CHECKING, Any

import numpy as np
from qualify_audio import load, pair, payload, signature


if TYPE_CHECKING:
    from collections.abc import Callable


ROOT = Path(__file__).resolve().parents[2]
sf: Any = importlib.import_module("soundfile")


def decode(module: Any, data: bytes, dtype: str, *, blocks: bool = False) -> object:
    """Consume a fresh stream through the real public wrapper."""
    if blocks:
        return list(module.blocks(io.BytesIO(data), dtype=dtype, blocksize=1024))
    return module.read(io.BytesIO(data), dtype=dtype)


def encode(module: Any, values: Any, subtype: str) -> bytes:
    """Include allocation, WAV framing, write and close."""
    output = io.BytesIO()
    module.write(output, values, 16000, subtype=subtype)
    return output.getvalue()


def result_signature(value: Any) -> object:
    """Compare all WAV bytes except a verified wall-clock PEAK timestamp.

    libsndfile FLOAT writes include creation time even between two baseline
    calls. Validate that field and normalize only its four bytes, outside timed
    batches. Retain format, peak magnitude/position and every sample byte.
    """
    if not isinstance(value, bytes) or value[:4] != b"RIFF":
        return signature(value)
    output = bytearray(value)
    offset = 12
    while offset + 8 <= len(value):
        name, size = struct.unpack_from("<4sI", value, offset)
        if name == b"PEAK":
            version, timestamp = struct.unpack_from("<II", value, offset + 8)
            if size != 16 or version != 1 or abs(timestamp - time.time()) > 2:
                msg = "unexpected WAV PEAK metadata"
                raise ValueError(msg)
            output[offset + 12 : offset + 16] = b"\0" * 4
        offset += 8 + size + size % 2
    return bytes(output)


def main() -> None:
    """Check exact results and retain alternating wall/current-thread CPU pairs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # The duplicate binding has independent globals/classes and uses the exact
    # installed Python/CFFI code; change only its explicit dlopen handle.
    candidate_sf = load(Path(sf.__file__), "_qualified_soundfile")
    candidate_sf._snd = candidate_sf._ffi.dlopen(str(args.candidate_library.resolve()))
    if candidate_sf.available_formats() != sf.available_formats():
        msg = "format set differs"
        raise ValueError(msg)
    for format_name in sf.available_formats():
        if candidate_sf.available_subtypes(format_name) != sf.available_subtypes(
            format_name
        ):
            msg = f"subtypes differ: {format_name}"
            raise ValueError(msg)
    wrapper = ROOT / "src/unstd/audio/wavx.py"
    baseline = load(wrapper, "_sndfile_before")
    candidate = load(wrapper, "_sndfile_after")
    candidate._sf = candidate_sf
    cases: list[tuple[str, Callable[[], object], Callable[[], object]]] = []
    for count in (0, 1, 16, 1024, 65536):
        for width in (1, 2, 4):
            data = payload(width, count)
            cases.extend(
                (
                    f"read/pcm{width * 8}/{count}/{dtype}",
                    partial(decode, baseline, data, dtype),
                    partial(decode, candidate, data, dtype),
                )
                for dtype in ("int16", "float32", "float64")
            )
        for dtype in ("int16", "float32"):
            data = payload(2, count)
            cases.append(
                (
                    f"blocks/pcm16/{count}/{dtype}",
                    partial(decode, baseline, data, dtype, blocks=True),
                    partial(decode, candidate, data, dtype, blocks=True),
                )
            )
        for subtype in ("PCM_16", "PCM_32", "FLOAT"):
            values = np.linspace(-0.9, 0.9, count, dtype=np.float64)
            cases.append(
                (
                    f"write/{subtype}/{count}",
                    partial(encode, baseline, values, subtype),
                    partial(encode, candidate, values, subtype),
                )
            )
    enabled = gc.isenabled()
    gc.disable()
    try:
        rows = [
            pair(name, a, b, result_signature=result_signature) for name, a, b in cases
        ]
    finally:
        if enabled:
            gc.enable()
    baseline_path = Path(sf._full_path)
    report = {
        "schema": 1,
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "soundfile": sf.__version__,
        "method": "seven alternating adjacent pairs, complete public calls, current-thread CPU",
        "result_checks": "exact samples and container bytes except validated FLOAT WAV PEAK creation timestamp; signature checks occur outside timed loops",
        "libraries": {
            side: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for side, path in (
                ("baseline", baseline_path),
                ("candidate", args.candidate_library),
            )
        },
        "sources": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                Path(__file__).with_name("qualify_audio.py"),
                Path(sf.__file__),
                wrapper,
            )
        },
        "formats_and_subtypes_equal": True,
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(rows)} exact checked public-call comparisons")


if __name__ == "__main__":
    main()
