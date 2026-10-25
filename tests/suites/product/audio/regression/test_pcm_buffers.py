"""WAV wrapping uses buffer byte size and returns independent immutable bytes."""

from __future__ import annotations

import io
import struct
import wave

import pytest

from unstd.audio import wavx


def _reference(raw, channels, width):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(24000)
        writer.writeframes(raw)
    return output.getvalue()


@pytest.mark.parametrize("buffer_type", ["bytes", "bytearray", "typed", "matrix"])
@pytest.mark.parametrize(("channels", "width"), [(1, 1), (2, 2), (3, 4)])
def test_wrap_pcm_buffers_match_stdlib_headers_and_payload(
    buffer_type, channels, width
):
    # Typed/multidimensional views have element counts unlike their byte size.
    payload = bytes(range(48))
    mutable = bytearray(payload)
    raw = {
        "bytes": payload,
        "bytearray": mutable,
        "typed": memoryview(mutable).cast("i"),
        "matrix": memoryview(mutable).cast("h", shape=[4, 6]),
    }[buffer_type]
    result = wavx.wrap_pcm(raw, 24000, channels=channels, width=width)
    expected = _reference(payload, channels, width)
    assert type(result) is bytes
    assert result == expected
    assert struct.unpack_from("<I", result, 4)[0] == 36 + len(payload)
    assert struct.unpack_from("<I", result, 40)[0] == len(payload)
    mutable[:] = bytes(len(mutable))
    assert result == expected


def test_wrap_pcm_accepts_a_contiguous_sliced_buffer():
    source = bytearray(range(24))
    raw = memoryview(source)[4:20].cast("h")
    result = wavx.wrap_pcm(raw, 24000, channels=2)
    assert result == _reference(bytes(range(4, 20)), 2, 2)


def test_wrap_pcm_rejects_noncontiguous_buffers():
    raw = memoryview(bytearray(range(16)))[::2]
    with pytest.raises(BufferError, match="contiguous"):
        wavx.wrap_pcm(raw, 24000)
