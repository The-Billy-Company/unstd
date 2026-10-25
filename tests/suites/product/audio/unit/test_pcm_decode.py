"""Raw PCM decoding preserves sample precision, interleave, and buffer ownership.

Expectations come from scalar integer arithmetic and hand-packed frames rather
than a WAV round trip, so shared codec mistakes cannot satisfy the contract.
"""

from __future__ import annotations

import struct

import pytest

from unstd.audio import wavx


_BACKENDS = ["array", "numpy"] if wavx._HAVE_NUMPY else ["array"]


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch):
    if request.param == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return request.param


def _values(samples):
    return samples.tolist() if hasattr(samples, "tolist") else samples


def _owned(samples, backend, channels, frames, dtype):
    if backend == "array":
        assert isinstance(samples, list)
    else:
        assert samples.shape == ((frames,) if channels == 1 else (frames, channels))
        assert str(samples.dtype) == dtype
        assert samples.flags.owndata
        assert samples.flags.writeable


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_every_pcm16_value_normalizes_exactly(backend, dtype):
    values = list(range(-32768, 32768))
    samples = wavx.decode_pcm(struct.pack("<65536h", *values), dtype=dtype)
    _owned(samples, backend, 1, 65536, dtype)
    assert _values(samples) == [value / 32768 for value in values]


@pytest.mark.parametrize("dtype", ["float32", "float64", "int16"])
@pytest.mark.parametrize("channels", [1, 2, 4])
def test_pcm16_interleave_and_rails(backend, dtype, channels):
    values = [-32768, -16384, -1, 0, 1, 16384, 32766, 32767]
    expected = values if dtype == "int16" else [value / 32768 for value in values]
    if channels > 1:
        expected = [expected[i : i + channels] for i in range(0, len(values), channels)]
    samples = wavx.decode_pcm(
        struct.pack("<8h", *values), channels=channels, dtype=dtype
    )
    _owned(samples, backend, channels, len(values) // channels, dtype)
    assert _values(samples) == expected


@pytest.mark.parametrize("width", [1, 4])
@pytest.mark.parametrize("dtype", ["float32", "float64", "int16"])
def test_other_widths_keep_precision_and_int16_rounding(backend, width, dtype):
    if width == 1:
        values = [0, 1, 127, 128, 129, 192, 254, 255]
        raw = bytes(values)
        normalized = [(value - 128) / 128 for value in values]
    else:
        # Around half steps at large magnitudes, a premature float32 conversion
        # loses the low integer bit and changes the eventual PCM16 rounding.
        values = [
            -(1 << 31),
            -1073774593,
            -98304,
            -32768,
            -1,
            0,
            1,
            32768,
            98304,
            1073774593,
            (1 << 31) - 2,
            (1 << 31) - 1,
        ]
        raw = struct.pack("<12i", *values)
        normalized = [value / (1 << 31) for value in values]
    if backend == "array":
        with pytest.raises(ValueError, match=r"audio.*extra"):
            wavx.decode_pcm(raw, width=width, dtype=dtype)
        return
    if dtype == "int16":
        expected = [
            max(-32768, min(32767, round(value * 32768))) for value in normalized
        ]
    elif dtype == "float32":
        expected = [
            struct.unpack("<f", struct.pack("<f", value))[0] for value in normalized
        ]
    else:
        expected = normalized
    samples = wavx.decode_pcm(raw, width=width, channels=2, dtype=dtype)
    _owned(samples, backend, 2, len(values) // 2, dtype)
    assert _values(samples) == [expected[i : i + 2] for i in range(0, len(values), 2)]


@pytest.mark.parametrize("dtype", ["float32", "float64", "int16"])
@pytest.mark.parametrize("channels", [1, 2, 6])
def test_empty_pcm_keeps_owned_channel_shape(backend, dtype, channels):
    samples = wavx.decode_pcm(b"", channels=channels, dtype=dtype)
    _owned(samples, backend, channels, 0, dtype)
    assert _values(samples) == []


@pytest.mark.parametrize("dtype", ["float32", "float64", "int16"])
@pytest.mark.parametrize("typed", [False, True])
def test_mutable_and_typed_buffers_do_not_alias_results(backend, dtype, typed):
    original = struct.pack("<6h", -32768, -1, 1, 8192, 16384, 32767)
    source = bytearray(original)
    raw = memoryview(source).cast("h", shape=[3, 2]) if typed else source
    samples = wavx.decode_pcm(raw, channels=2, dtype=dtype)
    second = wavx.decode_pcm(raw, channels=2, dtype=dtype)
    _owned(samples, backend, 2, 3, dtype)
    expected = 32767 if dtype == "int16" else 32767 / 32768
    source[-2:] = b"\x00\x00"
    assert samples[2][1] == expected
    samples[0][0] = 0
    assert second[0][0] == (-32768 if dtype == "int16" else -1.0)
    assert source[:2] == original[:2]


@pytest.mark.parametrize("soundfile", [False, True])
def test_numpy_pcm_decode_does_not_depend_on_soundfile(monkeypatch, soundfile):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", soundfile)
    monkeypatch.setattr(wavx, "_sf", None)
    samples = wavx.decode_pcm(struct.pack("<3h", -32768, 0, 32767))
    assert isinstance(samples, np.ndarray)
    assert samples.tolist() == [-1.0, 0.0, 32767 / 32768]


@pytest.mark.parametrize("dtype", ["int32", "float16", "", None])
def test_invalid_output_dtype_is_rejected(backend, dtype):
    with pytest.raises(ValueError, match="dtype"):
        wavx.decode_pcm(b"\x00\x00", dtype=dtype)


@pytest.mark.parametrize("channels", [0, -1, 1.5, "2", None])
def test_channel_count_must_be_a_positive_integer(backend, channels):
    with pytest.raises((TypeError, ValueError)):
        wavx.decode_pcm(b"", channels=channels)


@pytest.mark.parametrize("width", [0, 3, 5, 1.5, "2", None])
def test_unsupported_sample_width_is_rejected(backend, width):
    with pytest.raises((TypeError, ValueError)):
        wavx.decode_pcm(b"", width=width)


@pytest.mark.parametrize(
    ("channels", "raw"), [(1, b"\x00"), (2, b"\x00\x00"), (2, bytes(7))]
)
def test_partial_sample_or_channel_frame_is_rejected(backend, channels, raw):
    with pytest.raises(ValueError, match="whole number of frames"):
        wavx.decode_pcm(raw, channels=channels)


def test_noncontiguous_pcm_is_rejected(backend):
    raw = memoryview(bytearray(range(16)))[::2]
    with pytest.raises(BufferError, match="contiguous"):
        wavx.decode_pcm(raw)
