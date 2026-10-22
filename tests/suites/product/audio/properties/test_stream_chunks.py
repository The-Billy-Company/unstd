"""Chunk boundaries never change streamed PCM, on any backend.

Law: for any int16 frames, channel count and partition into ``Writer.write``
chunks, the finished file's data chunk is the hand-packed interleaved frames,
and its header counts them (stdlib ``wave`` is the oracle). Law: for any
positive ``blocksize``, ``blocks`` yields consecutive full blocks plus one
shorter remainder whose concatenation is exactly those frames.

Backends are forced inside the test body so Hypothesis never shares a
function-scoped fixture across generated examples.
"""

from __future__ import annotations

import contextlib
import io
from itertools import pairwise
import struct
import wave

from hypothesis import example, given
from hypothesis import strategies as st
import pytest

from unstd.audio import wavx


_BACKENDS = ["array"]
if wavx._HAVE_NUMPY:
    _BACKENDS.append("numpy")
if wavx._HAVE_SOUNDFILE:
    _BACKENDS.append("soundfile")

_I16 = st.integers(min_value=-32768, max_value=32767)


@contextlib.contextmanager
def _forced(which: str):
    saved = wavx._HAVE_SOUNDFILE, wavx._HAVE_NUMPY, wavx._np
    if which != "soundfile":
        wavx._HAVE_SOUNDFILE = False
    if which == "array":
        wavx._HAVE_NUMPY, wavx._np = False, None
    try:
        yield
    finally:
        wavx._HAVE_SOUNDFILE, wavx._HAVE_NUMPY, wavx._np = saved


@st.composite
def _streams(draw):
    channels = draw(st.integers(min_value=1, max_value=3))
    frames = draw(
        st.lists(st.lists(_I16, min_size=channels, max_size=channels), max_size=40)
    )
    cuts = sorted(draw(st.sets(st.integers(1, max(1, len(frames) - 1)))))
    bounds = [0, *(c for c in cuts if c < len(frames)), len(frames)]
    chunks = [frames[a:b] for a, b in pairwise(bounds) if b > a]
    return channels, frames, chunks


def _shape(chunk: list[list[int]], channels: int) -> list:
    return [frame[0] for frame in chunk] if channels == 1 else chunk


def _values(samples) -> list:
    return samples.tolist() if hasattr(samples, "tolist") else list(samples)


@pytest.mark.parametrize("which", _BACKENDS)
@given(stream=_streams())
@example(stream=(1, [[1], [-2], [3]], [[[1]], [[-2]], [[3]]]))
@example(stream=(3, [[1, 2, 3], [4, 5, 6]], [[[1, 2, 3]], [[4, 5, 6]]]))
def test_writer_chunk_boundaries_do_not_change_frames(which, stream) -> None:
    channels, frames, chunks = stream
    out = io.BytesIO()
    with _forced(which), wavx.Writer(out, 16000, channels=channels) as writer:
        for chunk in chunks:
            writer.write(_shape(chunk, channels))
    flat = [s for frame in frames for s in frame]
    with wave.open(io.BytesIO(out.getvalue()), "rb") as reference:
        assert reference.getnchannels() == channels
        assert reference.getnframes() == len(frames)
        assert reference.readframes(len(frames) + 1) == struct.pack(
            f"<{len(flat)}h", *flat
        )


@pytest.mark.parametrize("which", _BACKENDS)
@given(stream=_streams(), blocksize=st.integers(min_value=1, max_value=45))
@example(stream=(2, [[1, 2], [3, 4], [5, 6]], []), blocksize=2)
@example(stream=(1, [[7]], []), blocksize=45)
def test_blocks_partition_frames_at_every_blocksize(which, stream, blocksize) -> None:
    channels, frames, _ = stream
    flat = [s for frame in frames for s in frame]
    data = wavx.wrap_pcm(struct.pack(f"<{len(flat)}h", *flat), 8000, channels=channels)
    with _forced(which):
        blocks = [
            _values(b) for b in wavx.blocks(io.BytesIO(data), blocksize, dtype="int16")
        ]
    expected = [
        _shape(frames[i : i + blocksize], channels)
        for i in range(0, len(frames), blocksize)
    ]
    assert blocks == expected
