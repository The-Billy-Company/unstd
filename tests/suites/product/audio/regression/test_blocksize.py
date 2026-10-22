"""A nonpositive streaming block size cannot disable progress or bounded reads.

On stdlib fallbacks, -1 decoded the entire file and 0 silently dropped all audio.
The soundfile backend instead yielded empty blocks forever for 0. Reject these
values before opening or reading input, with real WAV controls for every backend.
"""

from __future__ import annotations

import struct

import pytest

from unstd.audio import wavx


_BACKENDS = ["array"]
if wavx._HAVE_NUMPY:
    _BACKENDS.append("numpy")
if wavx._HAVE_SOUNDFILE:
    _BACKENDS.append("soundfile")


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch):
    which = request.param
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return which


@pytest.fixture
def wav_path(tmp_path):
    path = tmp_path / "stream.wav"
    path.write_bytes(wavx.wrap_pcm(struct.pack("<5h", 1, -2, 3, -4, 5), 8000))
    return path


@pytest.mark.parametrize("blocksize", [0, -1])
def test_nonpositive_blocks_reject_before_reading(backend, wav_path, blocksize):
    with wav_path.open("rb") as source:
        chunks = wavx.blocks(source, blocksize, dtype="int16")
        try:
            # A single next() also bounds the historical zero-size failure:
            # soundfile returned empty blocks forever without advancing input.
            with pytest.raises(ValueError, match=r"^blocksize must be positive$"):
                next(chunks)
            assert source.tell() == 0
            assert not source.closed
        finally:
            chunks.close()


def test_positive_blocks_keep_chunk_boundaries_and_samples(backend, wav_path):
    with wav_path.open("rb") as source:
        chunks = list(wavx.blocks(source, 2, dtype="int16"))
        assert [len(chunk) for chunk in chunks] == [2, 2, 1]
        assert [int(value) for chunk in chunks for value in chunk] == [1, -2, 3, -4, 5]
        assert not source.closed
