"""A closed ``Writer`` refuses further chunks instead of crashing inside ``wave``.

``Writer.close`` closed the stdlib ``wave`` handle but kept referencing it, so
the fallback's "no open handle" guard never fired: a later ``write`` raised
``AttributeError`` from ``wave`` internals. soundfile raises its own closed-file
error. Either way the finished file must keep exactly its frames, as stdlib
``wave`` reads them.
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
def backend(request, monkeypatch):
    which = request.param
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)
    return which


def test_writer_refuses_writes_after_close(backend):
    sink = io.BytesIO()
    writer = wavx.Writer(sink, 8000)
    writer.write([0.5])
    writer.close()
    finished = sink.getvalue()
    if backend == "soundfile":
        import soundfile as sf

        with pytest.raises(sf.SoundFileRuntimeError, match="closed file"):
            writer.write([0.5])
    else:
        with pytest.raises(ValueError, match=r"^Writer has no open handle"):
            writer.write([0.5])
    writer.close()
    assert sink.getvalue() == finished
    sink.seek(0)
    with wave.open(sink, "rb") as reference:
        assert reference.getnframes() == 1
        assert reference.readframes(2) == (16384).to_bytes(2, "little")
