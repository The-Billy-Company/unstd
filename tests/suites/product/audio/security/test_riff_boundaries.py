"""Hand-built RIFF/WAVE containers at the parser boundary.

Malformed containers must fail exactly as the selected codec fails on the same
bytes: stdlib ``wave`` for both fallbacks, ``soundfile.read`` for the fast path.
``wavx`` may not decode them into samples, swallow the error, or close a
caller-owned source. Valid but unusual layouts (padded odd-sized chunks, chunks
after ``data``) and whole-frame truncation decode to the hand-packed samples.
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

_PCM = [0, 1, -2, 300, -32768, 32767, 7, -7]
_DATA = struct.pack("<8h", *_PCM)


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch) -> str:
    which = request.param
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return which


def _chunk(tag: bytes, body: bytes, size: int | None = None) -> bytes:
    pad = b"\0" * (len(body) % 2)
    return tag + struct.pack("<I", len(body) if size is None else size) + body + pad


def _riff(*chunks: bytes, form: bytes = b"WAVE", magic: bytes = b"RIFF") -> bytes:
    body = form + b"".join(chunks)
    return magic + struct.pack("<I", len(body)) + body


def _fmt(*, tag: int = 1, channels: int = 1, rate: int = 8000, bits: int = 16) -> bytes:
    block = channels * bits // 8
    return _chunk(
        b"fmt ", struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, bits)
    )


_VALID = _riff(_fmt(), _chunk(b"data", _DATA))
_CORRUPT = {
    "empty": b"",
    "magic-only": b"RIFF",
    "no-chunks": _riff(),
    "riff-magic": _riff(_fmt(), _chunk(b"data", _DATA), magic=b"RIFX"),
    "form-type": _riff(_fmt(), _chunk(b"data", _DATA), form=b"AVI "),
    "missing-data": _riff(_fmt()),
    "missing-fmt": _riff(_chunk(b"data", _DATA)),
    "data-before-fmt": _riff(_chunk(b"data", _DATA), _fmt()),
    "short-fmt": _riff(_chunk(b"fmt ", _fmt()[8:16]), _chunk(b"data", _DATA)),
    "mp3-format-tag": _riff(_fmt(tag=0x55), _chunk(b"data", _DATA)),
    "zero-channels": _riff(_fmt(channels=0), _chunk(b"data", _DATA)),
    "zero-bits": _riff(_fmt(bits=0), _chunk(b"data", _DATA)),
    "chunk-overruns-file": _riff(_fmt(), _chunk(b"LIST", b"info", size=4096)),
    "truncated-fmt": _VALID[:30],
}


def _outcome(read):
    try:
        samples = read()
    except Exception as error:  # The exception class is the observation.
        return type(error)
    return samples.tolist() if hasattr(samples, "tolist") else samples


def _codec_oracle(backend: str, data: bytes):
    """The codec's own verdict on *data*, without ``wavx`` in the path."""
    if backend == "soundfile":
        import soundfile as sf

        return _outcome(lambda: sf.read(io.BytesIO(data), dtype="int16")[0])

    def stdlib():
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.readframes(w.getnframes())

    return _outcome(stdlib)


@pytest.mark.parametrize("data", list(_CORRUPT.values()), ids=list(_CORRUPT))
def test_malformed_containers_fail_as_the_codec_fails(backend, data) -> None:
    expected = _codec_oracle(backend, data)
    if backend != "soundfile":
        assert isinstance(expected, type), "stdlib wave accepted a corrupt fixture"
    source = io.BytesIO(data)
    assert _outcome(lambda: wavx.read(source, dtype="int16")[0]) == expected
    assert not source.closed
    if isinstance(expected, type):
        source = io.BytesIO(data)
        with pytest.raises(expected):
            next(wavx.blocks(source, 3, dtype="int16"))
        assert not source.closed


def test_padded_odd_chunks_and_chunks_after_data_are_not_samples(backend) -> None:
    data = _riff(
        _chunk(b"LIST", b"odd"),
        _fmt(),
        _chunk(b"fact", struct.pack("<I", len(_PCM))),
        _chunk(b"data", _DATA),
        _chunk(b"junk", b"\x7f\x7f\x7f\x7f\x80\x80"),
    )
    samples, rate = wavx.read(io.BytesIO(data), dtype="int16")
    assert rate == 8000
    assert list(samples) == _PCM
    blocks = wavx.blocks(io.BytesIO(data), 3, dtype="int16")
    assert [list(b) for b in blocks] == [_PCM[:3], _PCM[3:6], _PCM[6:]]


@pytest.mark.parametrize(
    ("channels", "kept"), [(1, 3), (1, 0), (2, 2), (2, 1)], ids=str
)
def test_whole_frame_truncation_yields_exactly_the_frames_present(
    backend, channels, kept
) -> None:
    complete = _riff(_fmt(channels=channels), _chunk(b"data", _DATA))
    truncated = complete[: len(complete) - len(_DATA) + kept * channels * 2]
    with wave.open(io.BytesIO(truncated), "rb") as reference:
        assert reference.getnframes() == len(_PCM) // channels
        present = reference.readframes(len(_PCM))
    assert present == _DATA[: kept * channels * 2]
    samples, _ = wavx.read(io.BytesIO(truncated), dtype="int16")
    values = samples.tolist() if hasattr(samples, "tolist") else samples
    flat = _PCM[: kept * channels]
    assert values == (
        flat if channels == 1 else [flat[i : i + 2] for i in range(0, len(flat), 2)]
    )
