"""Integer PCM sample counts survive backend and source-width changes.

The public writer accepts integer sample counts, rather than normalized integer
signals. libsndfile scales int16/int32 inputs when the destination width differs,
so its input must first be clipped and converted to the destination's width.
Inspect frames with stdlib wave/struct: a writer and reader sharing the same
mistake must not make these regressions pass.
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
def backend(request, monkeypatch):
    """Exercise every available codec, including the pure-stdlib floor."""
    which = request.param
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return which


def _write_pcm(samples, subtype, streaming, channels=1):
    output = io.BytesIO()
    if streaming:
        with wavx.Writer(output, 16000, channels=channels, subtype=subtype) as writer:
            writer.write(samples[:1])
            writer.write(samples[1:])
    else:
        wavx.write(output, samples, 16000, subtype=subtype)
    assert not output.closed
    output.seek(0)
    with wave.open(output, "rb") as reference:
        assert reference.getframerate() == 16000
        assert reference.getnchannels() == channels
        assert reference.getnframes() == len(samples)
        width = reference.getsampwidth()
        raw = reference.readframes(reference.getnframes())
    return width, raw


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_pcm32_keeps_python_integer_sample_counts(backend, streaming):
    if backend == "array":
        with pytest.raises(ValueError, match="cannot write subtype"):
            _write_pcm([1000], "PCM_32", streaming)
        return
    width, raw = _write_pcm([1000], "PCM_32", streaming)
    assert width == 4
    assert raw == struct.pack("<i", 1000)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_32"])
def test_python_integer_overflow_clips_before_narrowing(backend, streaming, subtype):
    if backend == "array" and subtype == "PCM_32":
        with pytest.raises(ValueError, match="cannot write subtype 'PCM_32'"):
            _write_pcm([0], subtype, streaming)
        return
    scale = 1 << (15 if subtype == "PCM_16" else 31)
    samples = [-(1 << 100), -scale - 1, -scale, -1, 0, 1, scale - 1, scale, 1 << 100]
    expected = [-scale, -scale, -scale, -1, 0, 1, scale - 1, scale - 1, scale - 1]
    width, raw = _write_pcm(samples, subtype, streaming)
    assert width == (2 if subtype == "PCM_16" else 4)
    code = "h" if width == 2 else "i"
    assert raw == struct.pack(f"<{len(expected)}{code}", *expected)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_32"])
@pytest.mark.parametrize("dtype", ["int16", "int32", "uint64"])
def test_numpy_integer_width_does_not_scale_sample_counts(
    backend, streaming, subtype, dtype
):
    if backend == "array":
        pytest.skip("Typed NumPy inputs require a NumPy capability path")
    import numpy as np

    source = {
        "int16": [-32768, -1000, 0, 1000, 32767],
        "int32": [-(1 << 31), -100000, 0, 100000, (1 << 31) - 1],
        "uint64": [0, 1000, 1 << 32, (1 << 64) - 1],
    }[dtype]
    scale = 1 << (15 if subtype == "PCM_16" else 31)
    expected = [max(-scale, min(scale - 1, v)) for v in source]
    width, raw = _write_pcm(np.asarray(source, dtype=dtype), subtype, streaming)
    assert width == (2 if subtype == "PCM_16" else 4)
    code = "h" if width == 2 else "i"
    assert raw == struct.pack(f"<{len(expected)}{code}", *expected)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_pcm16_integer_clipping_preserves_stereo_interleave(backend, streaming):
    samples = [[-40000, 40000], [-1000, 1000], [32767, -32768]]
    width, raw = _write_pcm(samples, "PCM_16", streaming, channels=2)
    assert width == 2
    assert raw == struct.pack("<6h", -32768, 32767, -1000, 1000, 32767, -32768)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_mixed_signed_python_integers_do_not_become_normalized_floats(
    backend, streaming
):
    # NumPy infers float64 from negative ints mixed with unsigned-int64 values.
    # Even then -1 is one raw sample count, never normalized negative full scale.
    width, raw = _write_pcm([0, -1, 1 << 63], "PCM_16", streaming)
    assert width == 2
    assert raw == struct.pack("<3h", 0, -1, 32767)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_32"])
def test_float_samples_remain_normalized_and_clamped(backend, streaming, subtype):
    if backend == "array" and subtype == "PCM_32":
        with pytest.raises(ValueError, match="cannot write subtype 'PCM_32'"):
            _write_pcm([0], subtype, streaming)
        return
    scale = 1 << (15 if subtype == "PCM_16" else 31)
    samples = [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]
    expected = [-scale, -scale, -scale // 2, 0, scale // 2, scale - 1, scale - 1]
    width, raw = _write_pcm(samples, subtype, streaming)
    code = "h" if width == 2 else "i"
    assert raw == struct.pack(f"<{len(expected)}{code}", *expected)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
@pytest.mark.parametrize("subtype", ["PCM_24", "FLOAT"])
@pytest.mark.parametrize("dtype", ["int16", "int32", "float32", "float64"])
def test_soundfile_only_encodings_keep_backend_conversion(streaming, subtype, dtype):
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    values = [1000, -1000] if dtype.startswith("int") else [0.1, -0.2, 1.5]
    samples = np.asarray(values, dtype=dtype)
    expected = io.BytesIO()
    actual = io.BytesIO()
    if streaming:
        with sf.SoundFile(
            expected,
            mode="w",
            samplerate=16000,
            channels=1,
            subtype=subtype,
            format="WAV",
        ) as reference:
            reference.write(samples[:1])
            reference.write(samples[1:])
        with wavx.Writer(actual, 16000, subtype=subtype) as writer:
            writer.write(samples[:1])
            writer.write(samples[1:])
    else:
        sf.write(expected, samples, 16000, subtype=subtype, format="WAV")
        wavx.write(actual, samples, 16000, subtype=subtype)
    assert actual.getvalue() == expected.getvalue()
