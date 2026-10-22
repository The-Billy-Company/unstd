"""Truncated interleaved PCM frames raise a precise input error on fallbacks.

A retained deterministic fuzz case supplied six int16 samples for four channels.
The array floor indexed the incomplete second frame and raised IndexError; NumPy
reported incidental reshape errors. Both one-shot and streaming fallback reads
now reject partial sample/frame bytes with the same documented ValueError.
"""

from __future__ import annotations

import importlib.util
import io
import struct
import wave

import pytest

from unstd.audio import wavx


_HAVE_NUMPY = importlib.util.find_spec("numpy") is not None


@pytest.mark.parametrize("floor", ["array"] + (["numpy"] if _HAVE_NUMPY else []))
@pytest.mark.parametrize("dtype", ["int16", "float32", "float64"])
@pytest.mark.parametrize("operation", ["read", "blocks-1", "blocks-2"])
@pytest.mark.parametrize(
    ("channels", "trailing_bytes"),
    [(1, 1), (2, 1), (2, 2), (2, 3), (4, 1), (4, 2), (4, 4), (4, 6), (4, 7)],
)
def test_fallback_truncated_pcm_frames_are_explicit_rejections(
    floor, dtype, operation, channels, trailing_bytes, monkeypatch
):
    """A partial final frame cannot become an indexing error or ragged samples."""
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if floor == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    pcm = struct.pack(f"<{channels * 2}h", *range(channels * 2))
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((channels, 2, 48000, 0, "NONE", "not compressed"))
        writer.writeframes(pcm)
    complete = output.getvalue()
    truncated = complete[: 44 + channels * 2 + trailing_bytes]

    def decode(source):
        if operation == "read":
            return wavx.read(source, dtype=dtype)[0]
        return list(wavx.blocks(source, int(operation[-1]), dtype=dtype))

    source = io.BytesIO(truncated)
    with pytest.raises(ValueError, match=r"^PCM data is not a whole number of frames$"):
        decode(source)
    assert not source.closed
    source = io.BytesIO(complete)
    values = decode(source)
    assert not source.closed
    assert len(values) == (1 if operation == "blocks-2" else 2)


@pytest.mark.parametrize("floor", ["array"] + (["numpy"] if _HAVE_NUMPY else []))
@pytest.mark.parametrize(
    ("channels", "trailing_bytes"), [(1, 0), (1, 1), (2, 0), (2, 1), (2, 2)]
)
def test_fallback_declared_pcm_length_cannot_hide_partial_frames(
    floor, channels, trailing_bytes, monkeypatch
):
    """A data chunk's declared size must not floor away an incomplete frame."""
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if floor == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    pcm = struct.pack(f"<{channels * 2}h", *range(channels * 2))
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((channels, 2, 8000, 0, "NONE", "not compressed"))
        # Unlike the truncated-file regression above, the RIFF and data lengths
        # truthfully include the partial sample/channel bytes present on disk.
        writer.writeframes(pcm + b"\0" * trailing_bytes)
    source = io.BytesIO(output.getvalue())
    if trailing_bytes:
        with pytest.raises(
            ValueError, match=r"^PCM data is not a whole number of frames$"
        ):
            wavx.read(source, dtype="int16")
    else:
        samples, rate = wavx.read(source, dtype="int16")
        values = samples.tolist() if hasattr(samples, "tolist") else samples
        assert values == ([0, 1] if channels == 1 else [[0, 1], [2, 3]])
        assert rate == 8000
    assert not source.closed
