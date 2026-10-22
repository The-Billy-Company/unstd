"""Channel interleave and empty-file layouts, checked through stdlib ``wave`` bytes.

WAV interleaves one sample per channel per frame. Every expectation is the
hand-packed little-endian frame sequence; a deinterleave, transpose or channel
rotation bug changes those bytes or the decoded ``(frames, channels)`` rows.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from unstd.audio import wavx


_BACKENDS = ["array"]
if wavx._HAVE_NUMPY:
    _BACKENDS.append("numpy")
if wavx._HAVE_SOUNDFILE:
    _BACKENDS.append("soundfile")


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch) -> str:
    which = request.param
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return which


def _values(samples) -> list:
    return samples.tolist() if hasattr(samples, "tolist") else list(samples)


def _frames(channels: int, count: int) -> list[list[int]]:
    """Distinct per-channel ramps: sample = 1000·channel + frame, sign alternating."""
    return [
        [(-1) ** c * (1000 * (c + 1) + f) for c in range(channels)]
        for f in range(count)
    ]


def _inspect(data: bytes) -> tuple[int, int, bytes]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return w.getnchannels(), w.getnframes(), w.readframes(w.getnframes() + 1)


@pytest.mark.parametrize("channels", [3, 6])
@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_multichannel_frames_interleave_in_channel_order(
    backend, channels, streaming
) -> None:
    frames = _frames(channels, 5)
    out = io.BytesIO()
    if streaming:
        with wavx.Writer(out, 16000, channels=channels) as writer:
            writer.write(frames[:2])
            writer.write(frames[2:])
    else:
        wavx.write(out, frames, 16000)
    flat = [s for frame in frames for s in frame]
    assert _inspect(out.getvalue()) == (
        channels,
        5,
        struct.pack(f"<{len(flat)}h", *flat),
    )
    ints, _ = wavx.read(io.BytesIO(out.getvalue()), dtype="int16")
    assert _values(ints) == frames
    blocks = list(wavx.blocks(io.BytesIO(out.getvalue()), 2, dtype="int16"))
    assert [_values(b) for b in blocks] == [frames[:2], frames[2:4], frames[4:]]


def test_hand_interleaved_pcm_decodes_to_frame_rows(backend) -> None:
    flat = list(range(-6, 6))
    data = wavx.wrap_pcm(struct.pack("<12h", *flat), 8000, channels=4)
    ints, _ = wavx.read(io.BytesIO(data), dtype="int16")
    assert _values(ints) == [flat[0:4], flat[4:8], flat[8:12]]
    floats, _ = wavx.read(io.BytesIO(data), dtype="float64")
    assert _values(floats) == [[v / 32768 for v in flat[i : i + 4]] for i in (0, 4, 8)]


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_empty_mono_writes_a_zero_frame_header(backend, streaming) -> None:
    out = io.BytesIO()
    if streaming:
        with wavx.Writer(out, 8000):
            pass
    else:
        wavx.write(out, [], 8000)
    assert out.getvalue() == wavx.wrap_pcm(b"", 8000)
    assert _inspect(out.getvalue()) == (1, 0, b"")


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("dtype", ["float32", "int16"])
def test_zero_frame_files_decode_to_empty_samples(backend, channels, dtype) -> None:
    data = wavx.wrap_pcm(b"", 44100, channels=channels)
    samples, rate = wavx.read(io.BytesIO(data), dtype=dtype)
    assert rate == 44100
    assert _values(samples) == []
    if backend != "array":
        assert samples.shape == ((0,) if channels == 1 else (0, channels))
        assert str(samples.dtype) == dtype
    assert list(wavx.blocks(io.BytesIO(data), 4, dtype=dtype)) == []


@pytest.mark.parametrize("which", [b for b in _BACKENDS if b != "array"])
def test_empty_stereo_array_writes_a_two_channel_header(monkeypatch, which) -> None:
    import numpy as np

    if which == "numpy":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    out = io.BytesIO()
    wavx.write(out, np.zeros((0, 2), dtype=np.float32), 8000)
    assert out.getvalue() == wavx.wrap_pcm(b"", 8000, channels=2)
