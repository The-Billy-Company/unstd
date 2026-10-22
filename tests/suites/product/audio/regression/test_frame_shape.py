"""Frames whose width contradicts the channel layout are rejected on every backend.

NumPy and soundfile refused ragged multichannel lists and streamed chunks whose
channel count differed from the ``Writer``'s declared ``channels`` with
``ValueError``. The array floor wrote ragged lists as a partial final frame, and
both stdlib fallbacks appended mismatched chunks verbatim, so the RIFF header
described different frames than the data chunk carried. Inspect the sink with
stdlib ``wave``: a rejected write must leave no samples behind.
"""

from __future__ import annotations

import io
import wave

import pytest

from unstd.audio import wavx


_BACKENDS = ["array"]
if wavx._HAVE_NUMPY:
    _BACKENDS.append("numpy")
if wavx._HAVE_SOUNDFILE:
    _BACKENDS.append("soundfile")


@pytest.fixture(params=_BACKENDS)
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    which = request.param
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return which


@pytest.mark.parametrize(
    "samples",
    [
        [[0.25, -0.25], [0.5]],
        [[0.25, -0.25], [0.5, 0.5, 0.5]],
        [[1000, -1000], [2000, -2000], [3000]],
    ],
    ids=["short-frame", "long-frame", "short-last-int-frame"],
)
def test_ragged_multichannel_write_is_rejected(
    backend: str, samples: list[list[float]]
) -> None:
    sink = io.BytesIO()
    message = (
        "expected 2 samples per frame"
        if backend == "array"
        else "setting an array element with a sequence"
    )
    with pytest.raises(ValueError, match=message):
        wavx.write(sink, samples, 8000)
    assert not sink.closed
    assert sink.getvalue() == b""


@pytest.mark.parametrize(
    ("channels", "chunk"),
    [
        (2, [0.1, 0.2, 0.3]),
        (2, []),
        (1, [[0.1, 0.2]]),
        (2, [[0.1, 0.2, 0.3]]),
        (3, [[1000, -1000]]),
    ],
    ids=[
        "mono-into-stereo",
        "empty-mono-into-stereo",
        "stereo-into-mono",
        "3-into-2",
        "2-into-3",
    ],
)
def test_writer_rejects_chunks_with_another_channel_count(
    backend: str, channels: int, chunk: list[float] | list[list[float]]
) -> None:
    sink = io.BytesIO()
    good = [[0.5] * channels] if channels > 1 else [0.5]
    with wavx.Writer(sink, 8000, channels=channels) as writer:
        writer.write(good)
        message = (
            f"Expected {channels} channels, got"
            if backend == "soundfile"
            else f"expected {channels}-channel frames, got"
        )
        with pytest.raises(ValueError, match=message):
            writer.write(chunk)
        writer.write(good)
    assert not sink.closed
    sink.seek(0)
    with wave.open(sink, "rb") as reference:
        assert reference.getnchannels() == channels
        assert reference.getnframes() == 2
        frames = reference.readframes(3)
    assert frames == (16384).to_bytes(2, "little", signed=True) * (2 * channels)
