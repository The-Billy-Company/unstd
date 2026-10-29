"""Exact PCM quantization and decoding, against hand-built samples and stdlib ``wave``.

Expected codes come from the documented fixed-point rule (scale by ``2**(w-1)``,
clamp to the signed range) evaluated with exact arithmetic, never from a run of
``wavx``. The stdlib fallbacks round to nearest. The README allows soundfile to
differ by one quantization step, so it must land on one of the two neighbouring
codes. Decoders are fed raw PCM wrapped by stdlib ``wave``.
"""

from __future__ import annotations

from fractions import Fraction
from functools import partial
import io
import math
import struct
import sys
import wave

import pytest

from tests.support.environment.backends import require_backend
from unstd.audio import wavx


_BACKENDS = ["array"]
if wavx._HAVE_NUMPY:
    _BACKENDS.append("numpy")
if wavx._HAVE_SOUNDFILE:
    _BACKENDS.append("soundfile")
_FULL = {"PCM_16": 1 << 15, "PCM_32": 1 << 31}


def _force(monkeypatch, which: str) -> None:
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch) -> str:
    _force(monkeypatch, request.param)
    return request.param


def _wav(raw: bytes, *, width: int = 2, channels: int = 1, rate: int = 8000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)
    return out.getvalue()


def _codes(samples, subtype: str) -> list[int]:
    out = io.BytesIO()
    wavx.write(out, samples, 8000, subtype=subtype)
    out.seek(0)
    with wave.open(out, "rb") as w:
        width, n = w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    return list(struct.unpack(f"<{n}{'h' if width == 2 else 'i'}", raw))


def _values(samples) -> list:
    return samples.tolist() if hasattr(samples, "tolist") else list(samples)


def _offgrid(scale: int) -> list[Fraction]:
    """Exactly representable inputs a quarter step away from every tie and code."""
    ks = [
        0,
        1,
        7,
        100,
        scale // 2 - 1,
        scale - 2,
        -1,
        -7,
        -100,
        -scale // 2,
        -scale + 1,
    ]
    return [Fraction(k) + f for k in ks for f in (Fraction(1, 4), Fraction(3, 4))]


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_32"])
def test_off_grid_floats_round_to_the_nearest_code(backend, subtype) -> None:
    scale = _FULL[subtype]
    points = _offgrid(scale)
    samples = [float(p / scale) for p in points]
    assert all(Fraction(s) * scale == p for s, p in zip(samples, points, strict=True))
    if backend == "array" and subtype == "PCM_32":
        with pytest.raises(ValueError, match="cannot write subtype 'PCM_32'"):
            _codes(samples, subtype)
        return
    got = _codes(samples, subtype)
    nearest = [math.floor(p + Fraction(1, 2)) for p in points]
    if backend == "soundfile":
        assert all(
            math.floor(p) <= g <= math.ceil(p) for p, g in zip(points, got, strict=True)
        )
    else:
        assert got == nearest


def test_half_step_ties_land_on_a_neighbour_and_fallbacks_agree(monkeypatch) -> None:
    ties = [
        Fraction(2 * k + 1, 2) for k in (-32768, -1001, -2, -1, 0, 1, 2, 1000, 32766)
    ]
    samples = [float(t / 32768) for t in ties]
    results = {}
    for which in _BACKENDS:
        with monkeypatch.context() as patch:
            _force(patch, which)
            got = _codes(samples, "PCM_16")
        assert all(
            abs(Fraction(g) - t) == Fraction(1, 2)
            for g, t in zip(got, ties, strict=True)
        ), which
        results[which] = got
    fallbacks = {tuple(results[w]) for w in ("array", "numpy") if w in results}
    assert len(fallbacks) == 1


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_32"])
def test_full_scale_endpoints_and_overrange_clip_to_the_signed_rails(
    backend, subtype
) -> None:
    scale = _FULL[subtype]
    samples = [-1.0, 1.0, -1.0 - 2**-20, 1.0 + 2**-20, -1e30, 1e30, 1.0 - 1 / scale]
    expected = [-scale, scale - 1, -scale, scale - 1, -scale, scale - 1, scale - 1]
    if backend == "array" and subtype == "PCM_32":
        with pytest.raises(ValueError, match="cannot write subtype 'PCM_32'"):
            _codes(samples, subtype)
        return
    assert _codes(samples, subtype) == expected


def test_pcm16_decodes_to_exact_quotients(backend) -> None:
    pcm = [-32768, -32767, -16384, -1, 0, 1, 12345, 16384, 32767]
    data = _wav(struct.pack(f"<{len(pcm)}h", *pcm))
    for dtype in ("float32", "float64"):
        samples, rate = wavx.read(io.BytesIO(data), dtype=dtype)
        assert rate == 8000
        assert _values(samples) == [k / 32768 for k in pcm]
        if backend != "array":
            assert str(samples.dtype) == dtype
    ints, _ = wavx.read(io.BytesIO(data), dtype="int16")
    assert _values(ints) == pcm


def test_unsigned_8bit_decodes_around_its_128_midpoint(backend) -> None:
    if backend == "array":
        with pytest.raises(ValueError, match="supports 16-bit PCM"):
            wavx.read(io.BytesIO(_wav(bytes([128]), width=1)))
        return
    codes = [0, 1, 64, 127, 128, 129, 192, 255]
    data = _wav(bytes(codes), width=1)
    samples, _ = wavx.read(io.BytesIO(data), dtype="float64")
    assert _values(samples) == [(c - 128) / 128 for c in codes]
    ints, _ = wavx.read(io.BytesIO(data), dtype="int16")
    assert _values(ints) == [(c - 128) * 256 for c in codes]


def test_32bit_decodes_to_exact_quotients_and_shared_int16_codes(backend) -> None:
    pcm = [-(1 << 31), -3 << 16, 0, 5 << 16, (1 << 31) - (1 << 16), (1 << 31) - 1]
    data = _wav(struct.pack(f"<{len(pcm)}i", *pcm), width=4)
    if backend == "array":
        with pytest.raises(ValueError, match="supports 16-bit PCM"):
            wavx.read(io.BytesIO(data))
        return
    samples, _ = wavx.read(io.BytesIO(data), dtype="float64")
    assert _values(samples) == [k / (1 << 31) for k in pcm]
    ints, _ = wavx.read(io.BytesIO(data), dtype="int16")
    assert _values(ints) == [-32768, -3, 0, 5, 32767, 32767]


@pytest.mark.parametrize("writer", _BACKENDS)
def test_every_backend_reads_every_backends_pcm16_identically(
    monkeypatch, writer
) -> None:
    pcm = [-32768, -9000, -1, 0, 1, 9000, 32767]
    frames = [[v, -v if v > -32768 else 32767] for v in pcm]
    with monkeypatch.context() as patch:
        _force(patch, writer)
        out = io.BytesIO()
        wavx.write(out, frames, 22050)
    data = out.getvalue()
    with wave.open(io.BytesIO(data), "rb") as w:
        flat = [s for frame in frames for s in frame]
        assert w.readframes(w.getnframes()) == struct.pack(f"<{len(flat)}h", *flat)
    for reader in _BACKENDS:
        with monkeypatch.context() as patch:
            _force(patch, reader)
            ints, rate = wavx.read(io.BytesIO(data), dtype="int16")
            floats, _ = wavx.read(io.BytesIO(data), dtype="float64")
        assert rate == 22050
        assert _values(ints) == frames, reader
        assert _values(floats) == [[a / 32768, b / 32768] for a, b in frames], reader


# ── native-only subtypes: a separate capability contract ───────────────────


def _ieee_float_wav(values: list[float], *, width: int = 4) -> bytes:
    code = "f" if width == 4 else "d"
    data = bytearray(_wav(struct.pack(f"<{len(values)}{code}", *values), width=4))
    data[20:22] = struct.pack("<H", 3)  # WAVE_FORMAT_IEEE_FLOAT
    data[28:36] = struct.pack("<IHH", 8000 * width, width, width * 8)
    return bytes(data)


_PCM24 = [-(1 << 23), -5 << 8, 7 << 8, (1 << 23) - (1 << 8)]
_PCM24_RAW = b"".join(v.to_bytes(3, "little", signed=True) for v in _PCM24)


@pytest.mark.parametrize("width", [4, 8])
def test_soundfile_decodes_24bit_and_ieee_float_wavs(width) -> None:
    require_backend("soundfile")
    samples, _ = wavx.read(io.BytesIO(_wav(_PCM24_RAW, width=3)), dtype="float64")
    assert _values(samples) == [v / (1 << 23) for v in _PCM24]
    ints, _ = wavx.read(io.BytesIO(_wav(_PCM24_RAW, width=3)), dtype="int16")
    assert _values(ints) == [-32768, -5, 7, 32767]
    floats = [0.25, -0.5, 1.5, -2.0]
    samples, _ = wavx.read(
        io.BytesIO(_ieee_float_wav(floats, width=width)), dtype="float64"
    )
    assert _values(samples) == floats


@pytest.mark.parametrize("floor", ["array"] + (["numpy"] if wavx._HAVE_NUMPY else []))
def test_fallbacks_refuse_24bit_pcm_instead_of_misreading(monkeypatch, floor) -> None:
    _force(monkeypatch, floor)
    supported = "16" if floor == "array" else "8/16/32"
    with pytest.raises(
        ValueError, match=f"supports {supported}-bit PCM.*'audio' extra"
    ):
        wavx.read(io.BytesIO(_wav(_PCM24_RAW, width=3)))


@pytest.mark.parametrize("floor", ["array"] + (["numpy"] if wavx._HAVE_NUMPY else []))
@pytest.mark.parametrize("width", [4, 8])
@pytest.mark.parametrize("dtype", ["float32", "float64", "int16"])
@pytest.mark.parametrize("streaming", [False, True])
def test_fallbacks_refuse_ieee_float_instead_of_reinterpreting_integer_bits(
    monkeypatch, floor, width, dtype, streaming
) -> None:
    _force(monkeypatch, floor)
    data = _ieee_float_wav([0.25, -0.5, 1.5, -2.0], width=width)
    if sys.version_info >= (3, 15):
        # The stdlib now parses IEEE_FLOAT successfully. The fallback must
        # inspect the format before applying its integer fixed-point scaling.
        with wave.open(io.BytesIO(data), "rb") as reader:
            assert reader.getformat() == wave.WAVE_FORMAT_IEEE_FLOAT
            assert reader.getsampwidth() == width
            assert reader.getnframes() == 4
    source = io.BytesIO(data)
    operation = (
        partial(next, wavx.blocks(source, 2, dtype=dtype))
        if streaming
        else partial(wavx.read, source, dtype=dtype)
    )
    with pytest.raises(wave.Error, match=r"^unknown format: 3$"):
        operation()
    assert not source.closed
