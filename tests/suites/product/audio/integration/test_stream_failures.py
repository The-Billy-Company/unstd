"""Deterministic stream faults, unseekable sinks and early generator closure.

Each failing double first runs a clean control to count its calls; the
scenario then fails at every counted call in turn. Faults raised by a sink or
source must reach the caller as the same exception object, caller-owned
streams stay open, and handles ``wavx`` opened itself are closed.

soundfile drives Python file objects from cffi callbacks that report, rather
than raise, callback exceptions; identity is therefore a fallback contract.
Owned-file and descriptor checks run on every backend where they are observable.
"""

from __future__ import annotations

import builtins
import io
from pathlib import Path
import struct
import wave

import pytest

from unstd.audio import wavx


_FALLBACKS = ["array"] + (["numpy"] if wavx._HAVE_NUMPY else [])
_BACKENDS = _FALLBACKS + (["soundfile"] if wavx._HAVE_SOUNDFILE else [])
_PCM = [0, 1, -2, 300, -32768, 32767, 7, -7]
_FD_DIR = next((d for d in ("/proc/self/fd", "/dev/fd") if Path(d).is_dir()), None)


class _FaultError(Exception):
    """An application I/O failure whose identity must survive ``wavx``."""


def _force(monkeypatch, which: str) -> None:
    if which != "soundfile":
        monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    if which == "array":
        monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
        monkeypatch.setattr(wavx, "_np", None)


@pytest.fixture(params=_FALLBACKS)
def fallback(request, monkeypatch) -> str:
    _force(monkeypatch, request.param)
    return request.param


@pytest.fixture(params=_BACKENDS)
def backend(request, monkeypatch) -> str:
    _force(monkeypatch, request.param)
    return request.param


class _Sink(io.BytesIO):
    """Raises its fault on the *fail_at*-th write call (never, when ``None``)."""

    def __init__(self, fail_at: int | None = None) -> None:
        super().__init__()
        self.fail_at, self.writes, self.fault = fail_at, 0, _FaultError("sink write")

    def write(self, data) -> int:
        self.writes += 1
        if self.writes == self.fail_at:
            raise self.fault
        return super().write(data)


class _Source(io.BytesIO):
    """Raises its fault on the *fail_at*-th read call (never, when ``None``)."""

    def __init__(self, data: bytes, fail_at: int | None = None) -> None:
        super().__init__(data)
        self.fail_at, self.reads, self.fault = fail_at, 0, _FaultError("source read")

    def read(self, size=-1) -> bytes:
        self.reads += 1
        if self.reads == self.fail_at:
            raise self.fault
        return super().read(size)


def test_failure_doubles_follow_their_schedule() -> None:
    sink = _Sink(2)
    assert sink.write(b"a") == 1
    with pytest.raises(_FaultError):
        sink.write(b"b")
    assert sink.write(b"c") == 1
    assert sink.getvalue() == b"ac"
    source = _Source(b"xyz", 1)
    with pytest.raises(_FaultError):
        source.read(1)
    assert source.read(1) == b"x"


def _wav_bytes(samples: list[int] = _PCM) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return out.getvalue()


def _write_scenario(sink, streaming: bool) -> None:
    if streaming:
        with wavx.Writer(sink, 8000) as writer:
            writer.write(_PCM[:3])
            writer.write(_PCM[3:])
    else:
        wavx.write(sink, _PCM, 8000)


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_every_sink_write_failure_reaches_the_caller(fallback, streaming) -> None:
    control = _Sink()
    _write_scenario(control, streaming)
    assert control.getvalue() == _wav_bytes()
    assert control.writes > 1
    for fail_at in range(1, control.writes + 1):
        sink = _Sink(fail_at)
        with pytest.raises(_FaultError) as caught:
            _write_scenario(sink, streaming)
        assert caught.value is sink.fault, fail_at
        assert not sink.closed


@pytest.mark.parametrize("operation", ["read", "blocks"])
def test_every_source_read_failure_reaches_the_caller(fallback, operation) -> None:
    def decode(source):
        if operation == "read":
            return list(wavx.read(source, dtype="int16")[0])
        return [int(v) for b in wavx.blocks(source, 3, dtype="int16") for v in b]

    control = _Source(_wav_bytes())
    assert decode(control) == _PCM
    for fail_at in range(1, control.reads + 1):
        source = _Source(_wav_bytes(), fail_at)
        with pytest.raises(_FaultError) as caught:
            decode(source)
        assert caught.value is source.fault, fail_at
        assert not source.closed


# ── handles wavx opens itself ─────────────────────────────────────────────────


class _OwnedFile:
    """A real file handle whose write or close can be made to fail once."""

    def __init__(self, handle, fail_write_at: int | None, fail_close: bool) -> None:
        self._handle, self.writes = handle, 0
        self.fail_write_at, self.fail_close = fail_write_at, fail_close
        self.fault = _FaultError("owned file")

    def __getattr__(self, name: str):
        return getattr(self._handle, name)

    def write(self, data) -> int:
        self.writes += 1
        if self.writes == self.fail_write_at:
            raise self.fault
        return self._handle.write(data)

    def close(self) -> None:
        self._handle.close()
        if self.fail_close:
            raise self.fault


@pytest.fixture
def owned(monkeypatch, tmp_path):
    """Wrap every file ``wave`` opens under *tmp_path*; yields the opened wrappers."""
    opened: list[_OwnedFile] = []
    plan = {"fail_write_at": None, "fail_close": False}
    real_open = builtins.open

    def opener(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        if not str(file).startswith(str(tmp_path)):
            return handle
        opened.append(_OwnedFile(handle, **plan))
        return opened[-1]

    monkeypatch.setattr(builtins, "open", opener)
    return opened, plan


@pytest.mark.parametrize("streaming", [False, True], ids=["one-shot", "streaming"])
def test_owned_file_write_and_close_faults_propagate_and_release(
    fallback, owned, tmp_path, streaming
) -> None:
    opened, plan = owned
    _write_scenario(str(tmp_path / "control.wav"), streaming)
    total = opened[-1].writes
    assert (tmp_path / "control.wav").read_bytes() == _wav_bytes()
    schedules = [{"fail_write_at": n, "fail_close": False} for n in range(1, total + 1)]
    for schedule in [*schedules, {"fail_write_at": None, "fail_close": True}]:
        plan.update(schedule)
        with pytest.raises(_FaultError) as caught:
            _write_scenario(str(tmp_path / "out.wav"), streaming)
        assert caught.value is opened[-1].fault, schedule
        assert opened[-1].closed, schedule


@pytest.mark.parametrize("operation", ["read", "blocks"])
def test_owned_file_read_fault_closes_the_handle(
    fallback, monkeypatch, tmp_path, operation
) -> None:
    path = tmp_path / "in.wav"
    path.write_bytes(_wav_bytes())
    fault, opened = _FaultError("owned read"), []

    class Failing(io.BufferedReader):
        def read(self, size=-1):
            raise fault

    def opener(file, *_args, **_kwargs):
        opened.append(Failing(io.FileIO(file, "r")))
        return opened[-1]

    with monkeypatch.context() as patch, pytest.raises(_FaultError) as caught:  # noqa: PT012 — patched read or block creation is the operation under test.
        patch.setattr(builtins, "open", opener)
        if operation == "read":
            wavx.read(str(path))
        else:
            next(wavx.blocks(str(path), 3))
    assert caught.value is fault
    assert len(opened) == 1
    assert opened[0].closed


# ── unseekable sinks ──────────────────────────────────────────────────────────


class _Pipe(io.RawIOBase):
    """A write-only sink that, like a socket or pipe, cannot tell or seek."""

    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self.data += data
        return len(data)

    def tell(self) -> int:
        message = "tell"
        raise io.UnsupportedOperation(message)

    def seek(self, *_args) -> int:
        message = "seek"
        raise io.UnsupportedOperation(message)


def test_one_shot_write_needs_no_seek_on_fallbacks(fallback) -> None:
    pipe = _Pipe()
    wavx.write(pipe, _PCM, 8000)
    assert bytes(pipe.data) == _wav_bytes()
    assert not pipe.closed


def test_streamed_header_that_needs_patching_fails_loudly_on_a_pipe(fallback) -> None:
    pipe = _Pipe()
    writer = wavx.Writer(pipe, 8000)
    writer.write(_PCM[:3])
    writer.write(_PCM[3:])
    with pytest.raises(io.UnsupportedOperation):
        writer.close()
    writer.close()
    assert not pipe.closed
    with pytest.raises(ValueError, match="no open handle"):
        writer.write(_PCM)


# ── early generator closure ───────────────────────────────────────────────────


def _descriptors() -> int:
    assert _FD_DIR is not None
    return sum(path.name.isdecimal() for path in Path(_FD_DIR).iterdir())


@pytest.mark.skipif(_FD_DIR is None, reason="needs /proc/self/fd or /dev/fd")
def test_closing_a_block_iterator_early_releases_its_file(backend, tmp_path) -> None:
    path = tmp_path / "in.wav"
    path.write_bytes(_wav_bytes())
    before = _descriptors()
    chunks = wavx.blocks(str(path), 3, dtype="int16")
    assert [int(v) for v in next(chunks)] == _PCM[:3]
    assert _descriptors() == before + 1
    chunks.close()
    assert _descriptors() == before
    with pytest.raises(StopIteration):
        next(chunks)


def test_block_iterator_closure_does_not_wait_for_collection(
    fallback, monkeypatch, tmp_path
) -> None:
    path = tmp_path / "in.wav"
    path.write_bytes(_wav_bytes())
    readers: list[wave.Wave_read] = []
    real_open = wave.open

    def keep(*args, **kwargs):
        readers.append(real_open(*args, **kwargs))
        return readers[-1]

    monkeypatch.setattr(wave, "open", keep)
    chunks = wavx.blocks(str(path), 3, dtype="int16")
    next(chunks)
    assert readers[0].getfp() is not None
    chunks.close()
    assert readers[0].getfp() is None


@pytest.mark.skipif(_FD_DIR is None, reason="needs /proc/self/fd or /dev/fd")
def test_rejected_owned_files_release_their_descriptor(backend, tmp_path) -> None:
    path = tmp_path / "corrupt.wav"
    path.write_bytes(_wav_bytes()[:30])
    before = _descriptors()
    rejected = (EOFError, wave.Error, RuntimeError)  # per codec; see the security leaf
    with pytest.raises(rejected):
        wavx.read(str(path))
    with pytest.raises(rejected):
        next(wavx.blocks(str(path), 3))
    assert _descriptors() == before


def test_closing_a_block_iterator_leaves_a_caller_stream_usable(backend) -> None:
    source = io.BytesIO(_wav_bytes())
    chunks = wavx.blocks(source, 3, dtype="int16")
    next(chunks)
    chunks.close()
    assert not source.closed
    source.seek(0)
    assert [int(v) for v in wavx.read(source, dtype="int16")[0]] == _PCM
