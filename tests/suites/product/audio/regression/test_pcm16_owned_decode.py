"""Raw PCM16 decoding retains owned arrays without floating-point conversion.

Exhaustive sample codes and retained streaming chunks pin values, little-endian
layout, shape and ownership on the NumPy fallback. Other PCM widths still need
their original normalization and quantization, including half-step ties.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from tests.support.environment.backends import require_backend
from unstd.audio import wavx


@pytest.fixture
def numpy_fallback(monkeypatch):
    np = require_backend("numpy")
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    monkeypatch.setattr(wavx, "_HAVE_NUMPY", True)
    monkeypatch.setattr(wavx, "_np", np)
    return np


def _wav(raw, *, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((channels, width, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(raw)
    return output.getvalue()


def _owned_pcm16(samples, channels, frames):
    assert samples.dtype.str == "<i2"
    assert samples.shape == ((frames,) if channels == 1 else (frames, channels))
    assert samples.flags.owndata
    assert samples.flags.writeable


@pytest.mark.parametrize("channels", [1, 2, 4])
@pytest.mark.parametrize("streaming", [False, True], ids=["read", "blocks"])
def test_numpy_pcm16_all_codes_keep_values_and_owned_storage(
    numpy_fallback, channels, streaming
):
    raw = struct.pack("<65536h", *range(-32768, 32768))
    data = _wav(raw, channels=channels)
    source = io.BytesIO(data)
    if streaming:
        chunks = list(wavx.blocks(source, 257, dtype="int16"))
        for chunk in chunks:
            _owned_pcm16(chunk, channels, len(chunk))
        assert b"".join(chunk.tobytes() for chunk in chunks) == raw
        retained = chunks[-1].tobytes()
        chunks[0].flat[0] = 7
        assert chunks[-1].tobytes() == retained
        assert not numpy_fallback.shares_memory(chunks[0], chunks[-1])
    else:
        samples, rate = wavx.read(source, dtype="int16")
        assert rate == 16000
        _owned_pcm16(samples, channels, 65536 // channels)
        assert samples.tobytes() == raw
        samples.flat[0] = 7
    assert source.getvalue() == data
    assert not source.closed


@pytest.mark.parametrize("channels", [1, 2, 4])
def test_numpy_empty_pcm16_read_keeps_owned_channel_shape(numpy_fallback, channels):
    samples, rate = wavx.read(io.BytesIO(_wav(b"", channels=channels)), dtype="int16")
    assert rate == 16000
    _owned_pcm16(samples, channels, 0)


@pytest.mark.parametrize("raw_type", [bytes, bytearray])
def test_numpy_pcm16_result_does_not_alias_its_buffer(numpy_fallback, raw_type):
    original = struct.pack("<4h", -32768, -1, 1, 32767)
    raw = raw_type(original)
    samples = wavx._read_numpy(raw, 2, 2, "int16")
    _owned_pcm16(samples, 2, 2)
    samples[0, 0] = 123
    assert bytes(raw) == original
    if isinstance(raw, bytearray):
        raw[-2:] = b"\x00\x00"
    assert samples[1, 1] == 32767


def test_numpy_pcm16_bytes_subclass_retains_buffer_validation(numpy_fallback):
    class PCM(bytes):
        pass

    raw = PCM(struct.pack("<4h", -32768, -1, 1, 32767))
    samples = wavx._read_numpy(raw, 2, 2, "int16")
    _owned_pcm16(samples, 2, 2)
    assert samples.tobytes() == raw
    with pytest.raises(ValueError, match="PCM data is not a whole number of frames"):
        wavx._read_numpy(PCM(raw[:-1]), 2, 2, "int16")


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize(
    ("width", "raw", "expected"),
    [
        (
            1,
            bytes([0, 1, 127, 128, 129, 192, 254, 255]),
            [-32768, -32512, -256, 0, 256, 16384, 32256, 32512],
        ),
        (
            4,
            struct.pack(
                "<8i", -(1 << 31), -98304, -32768, -1, 0, 32768, 98304, (1 << 31) - 1
            ),
            [-32768, -2, 0, 0, 0, 0, 2, 32767],
        ),
    ],
    ids=["PCM8", "PCM32-ties"],
)
def test_numpy_other_pcm_widths_keep_int16_quantization(
    numpy_fallback, channels, width, raw, expected
):
    data = _wav(raw, channels=channels, width=width)
    samples, _ = wavx.read(io.BytesIO(data), dtype="int16")
    _owned_pcm16(samples, channels, len(expected) // channels)
    assert samples.tobytes() == struct.pack("<8h", *expected)
    chunks = list(wavx.blocks(io.BytesIO(data), 3, dtype="int16"))
    assert b"".join(chunk.tobytes() for chunk in chunks) == samples.tobytes()
