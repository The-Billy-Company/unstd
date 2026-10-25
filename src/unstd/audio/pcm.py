"""PCM scalar/frame validation and the pure-stdlib PCM16 conversion path."""

from __future__ import annotations

import array
import sys
from typing import TYPE_CHECKING


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable, Sequence
    import wave

    from unstd.audio.wavx import Samples


_DTYPES = ("float32", "float64", "int16")
_INSTALL_HINT = "install the 'audio' extra (soundfile): pip install 'unstd[audio]'"


def _python_integers(samples: object) -> bool:
    """Whether a plain Python sample tree contains only integer sample counts."""
    if isinstance(samples, int):
        return True
    return isinstance(samples, list | tuple) and all(map(_python_integers, samples))


def _pcm_buffer(raw: Buffer) -> bytes | bytearray | memoryview:
    """A contiguous byte buffer, retaining its owner until conversion finishes."""
    if isinstance(raw, bytes | bytearray):
        return raw
    view = memoryview(raw)
    if not view.c_contiguous:
        msg = "PCM buffer must be C-contiguous"
        raise BufferError(msg)
    # array.frombytes accepts byte views, including for a typed/multidimensional
    # source. Empty shaped views cannot be cast, and have no bytes to retain.
    return view.cast("B") if view.nbytes else memoryview(b"")


def _read_array(
    raw: bytes | bytearray | memoryview, nchannels: int, sampwidth: int, dtype: str
) -> Samples:
    if sampwidth != 2:
        raise ValueError(_bad_width(sampwidth, "16"))
    _check_pcm_frames(raw, nchannels, sampwidth)
    a = array.array("h")  # signed 16-bit
    a.frombytes(raw)
    if sys.byteorder == "big":  # WAV PCM is little-endian
        a.byteswap()
    conv: Callable[[int], float] = (
        (lambda v: v) if dtype == "int16" else (lambda v: v / 32768.0)
    )
    if nchannels == 1:
        return [conv(v) for v in a]
    return [
        [conv(a[i + c]) for c in range(nchannels)] for i in range(0, len(a), nchannels)
    ]


def _pcm16(samples: Samples) -> tuple[bytes, int]:
    """Little-endian PCM16 bytes of list samples, and their channel count."""
    flat, nchannels = _flatten(samples)
    ints = array.array("h", map(_to_int16, flat))
    if sys.byteorder == "big":
        ints.byteswap()
    return ints.tobytes(), nchannels


def _flatten(samples: Samples) -> tuple[list[float], int]:
    seq: list[object] = list(samples)
    if seq and isinstance(seq[0], list | tuple):
        nchannels = len(seq[0])
        return (
            [_scalar(v) for frame in seq for v in _frame(frame, nchannels)],
            nchannels,
        )
    return [_scalar(v) for v in seq], 1


def _frame(frame: object, nchannels: int) -> Sequence[float]:
    """Narrow one multichannel frame to a sequence — mixed mono/frame input is a bug."""
    if not isinstance(frame, list | tuple):
        msg = f"expected a per-frame sequence of samples, got {type(frame).__name__}"
        raise TypeError(msg)
    if len(frame) != nchannels:
        msg = f"expected {nchannels} samples per frame, got {len(frame)}"
        raise ValueError(msg)
    return frame


def _scalar(v: object) -> float:
    """Narrow one mono sample to a number — anything else is a bug."""
    if isinstance(v, int | float):
        return v
    msg = f"expected a numeric sample, got {type(v).__name__}"
    raise TypeError(msg)


def _to_int16(v: float) -> int:
    scaled = round(max(-1.0, min(1.0, v)) * 32768.0) if isinstance(v, float) else int(v)
    return max(-32768, min(32767, scaled))


def _check_pcm_frames(
    raw: bytes | bytearray | memoryview, nchannels: int, sampwidth: int
) -> None:
    """Reject truncated interleaved frames before decoding or channel indexing."""
    size = raw.nbytes if isinstance(raw, memoryview) else len(raw)
    if size % (nchannels * sampwidth):
        msg = "PCM data is not a whole number of frames"
        raise ValueError(msg)


def _check_channels(wav: wave.Wave_write, nchannels: int) -> None:
    """A streamed chunk must match the channel count fixed in the header."""
    if nchannels != (expected := wav.getnchannels()):
        msg = f"expected {expected}-channel frames, got {nchannels}"
        raise ValueError(msg)


def _check_dtype(dtype: str) -> None:
    if dtype not in _DTYPES:
        msg = f"dtype must be one of {_DTYPES}, got {dtype!r}"
        raise ValueError(msg)


def _bad_width(sampwidth: int, supported: str) -> str:
    return (
        f"unstd.audio stdlib fallback supports {supported}-bit PCM, got "
        f"{sampwidth * 8}-bit — {_INSTALL_HINT}"
    )


def _bad_subtype(subtype: str) -> str:
    return f"unstd.audio stdlib fallback cannot write subtype {subtype!r} — {_INSTALL_HINT}"
