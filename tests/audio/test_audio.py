"""Adversarial round-trip tests for ``unstd.audio.wavx``.

These pin the contract the voice paths depend on: a WAV written by ``write_wav``
reads back through ``read_wav`` with samplerate and sample values preserved
(within one PCM16 quantization step for float, *exactly* for int16), across mono
and multichannel, and a file written by the stdlib ``wave`` module decodes
consistently. Fixtures are synthesized in-test (a sine + a ramp) — no committed
binary blobs.

Every backend is exercised **independently of what is installed**: the ``backend``
fixture forces the pure-stdlib ``array`` floor and (when numpy is present) the
numpy fallback, and adds the ``soundfile`` fast path when the ``audio`` extra is
installed. So the fallback that ships in the base install is always under test,
even in a dev env that happens to have soundfile.
"""

from __future__ import annotations

import importlib.util
import io
import math
import struct
import wave

import pytest

from unstd.audio import wavx


_HAVE_SOUNDFILE = importlib.util.find_spec("soundfile") is not None
_HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
_PCM16_TOL = 1.0 / 32768  # one quantization step — the round-trip float tolerance

_BACKENDS = ["array"]  # the pure-stdlib floor always runs
if _HAVE_NUMPY:
    _BACKENDS.append("numpy")
if _HAVE_SOUNDFILE:
    _BACKENDS.append("soundfile")


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch) -> str:
    """Force ``wavx`` down a specific codec path so each is tested in isolation."""
    which = request.param
    if which == "array":  # pure-stdlib floor: no soundfile, no numpy
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    elif which == "numpy":  # stdlib wave + numpy decode
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    # "soundfile" → native selection (the extra is installed)
    return which


def _is_ndarray(obj) -> bool:
    return type(obj).__module__ == "numpy" and type(obj).__name__ == "ndarray"


def _sine(n: int, freq: float, sr: int, amp: float = 0.9) -> list[float]:
    """Return a mono float sine in (-1, 1); amp < 1 so no sample clips at full scale."""
    return [amp * math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


def _ramp(n: int, amp: float = 0.8) -> list[float]:
    """Return a rising sawtooth from -amp to +amp — a signal with a different shape."""
    return [amp * (2.0 * i / (n - 1) - 1.0) for i in range(n)]


# ── mono / stereo / int16 round-trips, one run per backend ─────────────────────


def test_roundtrip_mono_float_within_pcm16_tolerance(backend, tmp_path) -> None:
    """Test roundtrip mono float within pcm16 tolerance."""
    sr = 16000
    orig = _sine(512, freq=440, sr=sr)
    path = tmp_path / "mono.wav"
    wavx.write_wav(str(path), orig, sr)

    got, got_sr = wavx.read_wav(str(path))
    assert got_sr == sr
    assert len(got) == len(orig)
    # container-shape contract: ndarray on numpy/soundfile, list on the array floor
    assert _is_ndarray(got) == (backend != "array")
    for a, b in zip(orig, got, strict=True):
        assert abs(a - float(b)) <= _PCM16_TOL


def test_roundtrip_int16_is_exact(backend, tmp_path) -> None:
    # Integer input is written verbatim, so int16 round-trips bit-for-bit —
    # including both signed extremes.
    """Test roundtrip int16 is exact."""
    sr = 8000
    orig = [-32768, -12345, -1, 0, 1, 12345, 32767]
    path = tmp_path / "i16.wav"
    wavx.write_wav(str(path), orig, sr)

    got, got_sr = wavx.read_wav(str(path), dtype="int16")
    assert got_sr == sr
    assert [int(v) for v in got] == orig


def test_roundtrip_stereo_preserves_both_channels(backend, tmp_path) -> None:
    """Test roundtrip stereo preserves both channels."""
    sr = 22050
    left = _sine(384, freq=330, sr=sr)
    right = _ramp(384)
    orig = [[lft, rgt] for lft, rgt in zip(left, right, strict=True)]
    path = tmp_path / "stereo.wav"
    wavx.write_wav(str(path), orig, sr)

    got, got_sr = wavx.read_wav(str(path))
    assert got_sr == sr
    assert len(got) == len(orig)
    for (l_o, r_o), frame in zip(orig, got, strict=True):
        assert abs(l_o - float(frame[0])) <= _PCM16_TOL
        assert abs(r_o - float(frame[1])) <= _PCM16_TOL
    # The two channels carry genuinely different signals (guards against a
    # deinterleave bug that would collapse or swap them).
    assert any(abs(float(f[0]) - float(f[1])) > 0.1 for f in got)


def test_samplerate_is_preserved_across_rates(backend, tmp_path) -> None:
    """Test samplerate is preserved across rates."""
    tone = _sine(200, freq=200, sr=48000)
    for sr in (8000, 16000, 44100, 48000):
        path = tmp_path / f"sr_{sr}.wav"
        wavx.write_wav(str(path), tone, sr)
        _, got_sr = wavx.read_wav(str(path))
        assert got_sr == sr


# ── consistency with a file the stdlib `wave` module wrote ─────────────────────


def test_reads_file_written_by_stdlib_wave(backend, tmp_path) -> None:
    """Test reads file written by stdlib wave."""
    sr = 16000
    pcm = [0, 8192, -8192, 16384, -16384, 32767, -32768]
    path = tmp_path / "std.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))

    got_i16, got_sr = wavx.read_wav(str(path), dtype="int16")
    assert got_sr == sr
    assert [int(v) for v in got_i16] == pcm

    got_f, _ = wavx.read_wav(str(path))  # default float32
    for i, f in zip(pcm, got_f, strict=True):
        assert abs(float(f) - i / 32768.0) <= 1e-6


# ── wrap_pcm: a header, and nothing else ──────────────────────────────────────


def test_wrap_pcm_leaves_the_payload_byte_identical(backend) -> None:
    """The whole point of staying in bytes: no scale, no clamp, no requantize.

    Driven with values chosen to be destroyed by a float round trip — the signed
    rails, and a byte pattern that is not a valid sample sequence at all. If
    ``wrap_pcm`` ever grows a conversion, one of these moves.
    """
    raw = struct.pack("<7h", 0, 1, -1, 32767, -32768, 21845, -21846) + bytes(range(256))
    got = wavx.wrap_pcm(raw, 16000)
    with wave.open(io.BytesIO(got), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.readframes(w.getnframes()) == raw


def test_wrap_pcm_carries_the_interleave_and_width_it_was_given(backend) -> None:
    """Channels and width belong to the bytes, and a header cannot infer them."""
    raw = struct.pack("<8h", *range(8))
    with wave.open(io.BytesIO(wavx.wrap_pcm(raw, 8000, channels=2)), "rb") as w:
        # Same bytes, half the frames — stereo is the claim being made about them.
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (2, 8000, 4)
    with wave.open(io.BytesIO(wavx.wrap_pcm(raw, 8000, width=4)), "rb") as w:
        assert (w.getsampwidth(), w.getnframes()) == (4, 4)


def test_wrap_pcm_output_reads_back_through_read_wav(backend, tmp_path) -> None:
    """The container is real, not just well-formed — the seam's own reader agrees."""
    pcm = [0, 8192, -8192, 32767, -32768]
    path = tmp_path / "wrapped.wav"
    path.write_bytes(wavx.wrap_pcm(struct.pack(f"<{len(pcm)}h", *pcm), 24000))
    samples, sr = wavx.read_wav(str(path), dtype="int16")
    assert sr == 24000
    assert [int(v) for v in samples] == pcm


def test_wrap_pcm_treats_zero_channels_as_mono(backend) -> None:
    """A caller forwarding an unset channel count gets mono, not a raise.

    ``wave`` rejects 0 channels outright; the floor exists because the spec
    objects this reads from default the field to 0 when the source never said.
    """
    with wave.open(
        io.BytesIO(wavx.wrap_pcm(b"\x00\x00", 16000, channels=0)), "rb"
    ) as w:
        assert w.getnchannels() == 1


# ── adversarial: fail loud, never silently degrade ─────────────────────────────


def test_read_rejects_unknown_dtype(tmp_path) -> None:
    """Unknown dtype fails closed — file bytes must remain readable as float32."""
    path = tmp_path / "x.wav"
    wavx.write_wav(str(path), _sine(16, freq=100, sr=8000), 8000)
    with pytest.raises(ValueError, match="dtype must be one of"):
        wavx.read_wav(str(path), dtype="float16")
    got, sr = wavx.read_wav(str(path))  # contract still serves the default path
    assert sr == 8000
    assert len(got) == 16


@pytest.mark.parametrize("floor", ["array"] + (["numpy"] if _HAVE_NUMPY else []))
def test_fallback_rejects_unsupported_subtype(floor, monkeypatch, tmp_path) -> None:
    # The stdlib fallback (numpy: PCM_16/PCM_32; array floor: PCM_16) must raise
    # on an unsupported subtype rather than silently emit the wrong encoding.
    """Test fallback rejects unsupported subtype."""
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if floor == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    with pytest.raises(ValueError, match="cannot write subtype"):
        wavx.write_wav(
            str(tmp_path / "x.wav"), [0.0, 0.1, -0.1], 8000, subtype="PCM_24"
        )


def test_array_floor_rejects_non_16bit_read(monkeypatch, tmp_path) -> None:
    # A 32-bit file exists, but the pure-stdlib array floor supports only 16-bit —
    # it must point at the extra, not misread the bytes.
    """Test array floor rejects non 16bit read."""
    sr = 8000
    path = tmp_path / "w32.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(sr)
        w.writeframes(struct.pack("<3i", 0, 1 << 20, -(1 << 20)))
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
    monkeypatch.setattr(wavx, "_np", None)
    with pytest.raises(ValueError, match="supports 16-bit PCM"):
        wavx.read_wav(str(path))


# ── streaming: WavWriter + read_blocks, one run per backend ────────────────────


def _chunked(seq: list, size: int) -> list[list]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def test_wavwriter_streamed_chunks_match_write_wav_in_one_call(
    backend, tmp_path
) -> None:
    """Test wavwriter streamed chunks match write wav in one call."""
    sr = 16000
    orig = _sine(777, freq=440, sr=sr)  # not a multiple of the chunk size
    whole_path = tmp_path / "whole.wav"
    wavx.write_wav(str(whole_path), orig, sr)

    streamed_path = tmp_path / "streamed.wav"
    with wavx.WavWriter(str(streamed_path), sr) as w:
        for chunk in _chunked(orig, 100):
            w.write(chunk)

    whole, whole_sr = wavx.read_wav(str(whole_path))
    streamed, streamed_sr = wavx.read_wav(str(streamed_path))
    assert streamed_sr == whole_sr == sr
    assert len(streamed) == len(whole) == len(orig)
    for a, b in zip(whole, streamed, strict=True):
        assert abs(float(a) - float(b)) <= _PCM16_TOL


def test_wavwriter_finalizes_a_well_formed_header_on_close(backend, tmp_path) -> None:
    # writeframesraw defers the RIFF/data sizes to close() — this pins that the
    # header actually gets patched rather than left at its placeholder size.
    """Test wavwriter finalizes a well formed header on close."""
    sr = 8000
    orig = _sine(500, freq=300, sr=sr)
    path = tmp_path / "finalized.wav"
    with wavx.WavWriter(str(path), sr) as w:
        for chunk in _chunked(orig, 137):
            w.write(chunk)

    with wave.open(str(path), "rb") as w:
        assert w.getnframes() == len(orig)
        assert w.getframerate() == sr
        assert w.getnchannels() == 1
        assert len(w.readframes(w.getnframes())) == len(orig) * 2  # PCM16


def test_wavwriter_stereo_preserves_both_channels(backend, tmp_path) -> None:
    """Test wavwriter stereo preserves both channels."""
    sr = 16000
    left = _sine(300, freq=330, sr=sr)
    right = _ramp(300)
    frames = list(zip(left, right, strict=True))
    path = tmp_path / "stereo_stream.wav"
    with wavx.WavWriter(str(path), sr, channels=2) as w:
        for chunk in _chunked(frames, 64):
            w.write([list(f) for f in chunk])

    got, got_sr = wavx.read_wav(str(path))
    assert got_sr == sr
    assert len(got) == len(frames)
    for (l_o, r_o), frame in zip(frames, got, strict=True):
        assert abs(l_o - float(frame[0])) <= _PCM16_TOL
        assert abs(r_o - float(frame[1])) <= _PCM16_TOL


@pytest.mark.parametrize("floor", ["array"] + (["numpy"] if _HAVE_NUMPY else []))
def test_wavwriter_rejects_unsupported_subtype(floor, monkeypatch, tmp_path) -> None:
    """Test wavwriter rejects unsupported subtype."""
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if floor == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    with pytest.raises(ValueError, match="cannot write subtype"):
        wavx.WavWriter(str(tmp_path / "x.wav"), 8000, subtype="PCM_24")


def test_read_blocks_concatenates_to_the_same_samples_as_read_wav(
    backend, tmp_path
) -> None:
    """Test read blocks concatenates to the same samples as read wav."""
    sr = 16000
    orig = _sine(777, freq=440, sr=sr)  # deliberately not a multiple of blocksize
    path = tmp_path / "blocks.wav"
    wavx.write_wav(str(path), orig, sr)

    whole, _ = wavx.read_wav(str(path))
    blocks = list(wavx.read_blocks(str(path), 100))
    assert sum(len(b) for b in blocks) == len(orig)
    assert all(len(b) == 100 for b in blocks[:-1])  # every block but the last is full
    assert 0 < len(blocks[-1]) <= 100
    flattened = [v for block in blocks for v in block]
    for a, b in zip(whole, flattened, strict=True):
        assert abs(float(a) - float(b)) <= _PCM16_TOL


def test_read_blocks_rejects_unknown_dtype(tmp_path) -> None:
    """Test read blocks rejects unknown dtype."""
    path = tmp_path / "x.wav"
    wavx.write_wav(str(path), _sine(16, freq=100, sr=8000), 8000)
    with pytest.raises(ValueError, match="dtype must be one of"):
        list(wavx.read_blocks(str(path), 4, dtype="float16"))
