"""Quantization-theorem and clamp laws for ``unstd.audio.wavx`` PCM round-trips.

Law (positive, tight): a uniform mid-tread PCM quantizer has round-trip error
bounded by **half a least-significant step** — ``|x - dequant(quant(x))| <= Δ/2``
where ``Δ = 1/2**(w-1)`` (the *quantization theorem*, the independent authority;
Gray & Neuhoff, "Quantization", IEEE Trans. Inf. Theory 1998, §II). For PCM16
this is ``1/65536`` — strictly tighter than one full step. A truncating (``int``
instead of round-to-nearest) or mis-scaled encoder would exceed it.

Law (positive): a round-trip preserves array **shape and channel count** — mono
stays 1-D ``(frames,)``, N-channel stays 2-D ``(frames, N)``.

Law (adverse): an out-of-range sample **clamps, never wraps** — ``x > +1``
saturates toward ``+full_scale`` (staying positive) and ``x < -1`` toward
``-1.0`` (staying negative). A two's-complement wrap bug would flip the sign,
which this rejects.

The stdlib ``wave`` + numpy fallback (the codec ``unstd`` owns and can fix) is
forced under test regardless of whether the ``audio`` extra is installed; its
scaling contract is documented in ``wavx``'s module docstring.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import tempfile

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from unstd.audio import wavx


np = pytest.importorskip("numpy")  # the fallback path requires numpy

_FULL_SCALE = 1 << 15  # PCM16 magnitude, 2**(w-1) with w=16
_STEP = 1.0 / _FULL_SCALE  # Δ — one quantization step
_HALF_LSB = _STEP / 2.0  # the theorem's error ceiling
# Representable mid-tread input range: [-1, 1) minus the top code, so
# round(x·2^15) never saturates and the Δ/2 bound is the *tight* one.
_MAX_IN = 1.0 - _STEP
_SAMPLE = st.floats(
    min_value=-1.0, max_value=_MAX_IN, allow_nan=False, allow_infinity=False
)
# Out-of-range magnitudes that must clamp (both polarities), never wrap.
_OVERFLOW = st.floats(
    min_value=1.0 + 1e-3, max_value=64.0, allow_nan=False
) | st.floats(min_value=-64.0, max_value=-1.0 - 1e-3, allow_nan=False)


@contextlib.contextmanager
def _fallback_codec():
    """Force the stdlib+numpy path (the code unstd owns); restore globals after."""
    saved = wavx._HAVE_SOUNDFILE
    wavx._HAVE_SOUNDFILE = False
    try:
        yield
    finally:
        wavx._HAVE_SOUNDFILE = saved


def _roundtrip(samples: object) -> object:
    """Write ``samples`` to a temp WAV (PCM16) and read them back as float32."""
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        wavx.write(path, samples, 16000)
        got, _sr = wavx.read(path)
        return got
    finally:
        with contextlib.suppress(OSError):
            Path(path).unlink()


@given(samples=st.lists(_SAMPLE, min_size=1, max_size=64))
@example(samples=[0.0])
@example(samples=[_MAX_IN, -1.0])  # both boundary codes
@example(samples=[0.5 * _STEP])  # sits exactly on a decision boundary
def test_pcm16_roundtrip_within_half_lsb(samples: list[float]) -> None:
    """Round-trip error obeys the ½-LSB quantization theorem, not just 1 LSB."""
    with _fallback_codec():
        got = _roundtrip(samples)

    assert got.shape == (len(samples),)  # mono shape preserved
    for original, decoded in zip(samples, got, strict=True):
        assert abs(original - float(decoded)) <= _HALF_LSB + 1e-12


@given(frames=st.lists(st.tuples(_SAMPLE, _SAMPLE), min_size=1, max_size=48))
@example(frames=[(0.25, -0.25)])
def test_stereo_roundtrip_preserves_shape_and_channels(
    frames: list[tuple[float, float]],
) -> None:
    """A 2-D (frames, 2) array survives with shape and per-channel values intact."""
    orig = np.array(frames, dtype=np.float64)
    with _fallback_codec():
        got = _roundtrip(orig)

    assert got.shape == (len(frames), 2)  # frames x channels preserved
    for (left, right), (dl, dr) in zip(frames, got, strict=True):
        assert abs(left - float(dl)) <= _HALF_LSB + 1e-12
        assert abs(right - float(dr)) <= _HALF_LSB + 1e-12


@given(samples=st.lists(_OVERFLOW, min_size=1, max_size=48))
@example(samples=[5.0])  # positive overflow must not wrap to negative
@example(samples=[-5.0])  # negative overflow must not wrap to positive
def test_out_of_range_samples_clamp_never_wrap(samples: list[float]) -> None:
    """Saturation preserves polarity; a two's-complement wrap would flip the sign."""
    with _fallback_codec():
        got = _roundtrip(samples)

    for original, decoded in zip(samples, got, strict=True):
        value = float(decoded)
        if original > 1.0:
            assert value > 0.99, f"positive overflow wrapped to {value}"
        else:
            assert value < -0.99, f"negative overflow wrapped to {value}"
