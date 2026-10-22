"""Scheduled gzip stream faults, owned-file release and early closure.

A clean control run counts the double's calls; the scenario then fails at each
counted call. The application's exception object must reach the caller, a
caller-owned stream stays open, and the gzip file ends closed with a second
``close()`` as a no-op. Files ``gzipx`` opens are wrapped at ``builtins.open``
so their release is observed directly; descriptor counts cover early closure.
"""

from __future__ import annotations

import builtins
import gzip
import io
from pathlib import Path

import pytest

from tests.support.environment.backends import require_backend
from unstd.compress import gzipx


PAYLOAD = b"row of text\n" * 400 + bytes(range(256)) * 8
_FD_DIR = next((d for d in ("/proc/self/fd", "/dev/fd") if Path(d).is_dir()), None)


class _FaultError(Exception):
    """An application I/O failure whose identity must survive ``gzipx``."""


class _Sink(io.BytesIO):
    def __init__(self, fail_at: int | None = None) -> None:
        super().__init__()
        self.fail_at, self.writes, self.fault = fail_at, 0, _FaultError("sink write")

    def write(self, data) -> int:
        self.writes += 1
        if self.writes == self.fail_at:
            raise self.fault
        return super().write(data)


class _Source(io.BytesIO):
    def __init__(self, data: bytes, fail_at: int | None = None) -> None:
        super().__init__(data)
        self.fail_at, self.reads, self.fault = fail_at, 0, _FaultError("source read")

    def read(self, size=-1) -> bytes:
        self.reads += 1
        if self.reads == self.fail_at:
            raise self.fault
        return super().read(size)


def test_failure_doubles_follow_their_schedule() -> None:
    sink = _Sink(1)
    with pytest.raises(_FaultError):
        sink.write(b"a")
    assert sink.write(b"b") == 1
    assert sink.getvalue() == b"b"
    source = _Source(b"xy", 2)
    assert source.read(1) == b"x"
    with pytest.raises(_FaultError):
        source.read(1)


def _write(sink) -> list[gzipx.GzipFile]:
    created = []
    with gzipx.GzipFile(fileobj=sink, mode="wb", mtime=0) as handle:
        created.append(handle)
        for start in range(0, len(PAYLOAD), 1000):
            handle.write(PAYLOAD[start : start + 1000])
        handle.flush()
        handle.write(b"tail")
    return created


def test_every_sink_write_failure_reaches_the_caller() -> None:
    control = _Sink()
    _write(control)
    assert gzip.decompress(control.getvalue()) == PAYLOAD + b"tail"
    assert control.writes > 3
    for fail_at in range(1, control.writes + 1):
        sink = _Sink(fail_at)
        created: list[gzipx.GzipFile] = []
        with pytest.raises(_FaultError) as caught:
            created = _write(sink)
        assert caught.value is sink.fault, fail_at
        assert not sink.closed
        for handle in created:
            assert handle.closed
            handle.close()


def _read(source) -> bytes:
    with gzipx.GzipFile(fileobj=source, mode="rb") as handle:
        return b"".join(iter(lambda: handle.read(700), b""))


def test_every_source_read_failure_reaches_the_caller() -> None:
    member = gzip.compress(PAYLOAD, mtime=0) + gzip.compress(b"second", mtime=0)
    control = _Source(member)
    assert _read(control) == PAYLOAD + b"second"
    assert control.reads > 2
    for fail_at in range(1, control.reads + 1):
        source = _Source(member, fail_at)
        with pytest.raises(_FaultError) as caught:
            _read(source)
        assert caught.value is source.fault, fail_at
        assert not source.closed


# ── handles gzipx opens itself ────────────────────────────────────────────────


class _Owned:
    """A real file whose Nth write, Nth read or close can be made to fail."""

    def __init__(self, handle, plan: dict) -> None:
        self._handle, self.writes, self.reads = handle, 0, 0
        self.plan, self.fault = dict(plan), _FaultError("owned file")

    def __getattr__(self, name: str):
        return getattr(self._handle, name)

    def write(self, data) -> int:
        self.writes += 1
        if self.writes == self.plan["write"]:
            raise self.fault
        return self._handle.write(data)

    def read(self, size=-1) -> bytes:
        self.reads += 1
        if self.reads == self.plan["read"]:
            raise self.fault
        return self._handle.read(size)

    def close(self) -> None:
        self._handle.close()
        if self.plan["close"]:
            raise self.fault


@pytest.fixture
def owned(monkeypatch, tmp_path):
    opened: list[_Owned] = []
    plan = {"write": None, "read": None, "close": False}
    real_open = builtins.open

    def opener(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        if not str(file).startswith(str(tmp_path)):
            return handle
        opened.append(_Owned(handle, plan))
        return opened[-1]

    monkeypatch.setattr(builtins, "open", opener)
    return opened, plan


@pytest.mark.parametrize("text", [False, True], ids=["binary", "text"])
def test_owned_write_faults_propagate_and_release_the_file(
    owned, tmp_path, text
) -> None:
    opened, plan = owned
    path = tmp_path / "out.gz"
    value = PAYLOAD.decode("latin-1") if text else PAYLOAD

    def scenario() -> None:
        options = {"encoding": "latin-1", "newline": ""} if text else {}
        with gzipx.open(path, "wt" if text else "wb", **options) as handle:
            handle.write(value)

    scenario()
    total = opened[-1].writes
    assert total > 3
    assert gzip.decompress(path.read_bytes()) == PAYLOAD
    plans = [{"write": n, "read": None, "close": False} for n in range(1, total + 1)]
    for schedule in [*plans, {"write": None, "read": None, "close": True}]:
        plan.update(schedule)
        with pytest.raises(_FaultError) as caught:
            scenario()
        assert caught.value is opened[-1].fault, schedule
        assert opened[-1].closed, schedule


def test_owned_read_faults_propagate_and_release_the_file(owned, tmp_path) -> None:
    opened, plan = owned
    path = tmp_path / "in.gz"
    path.write_bytes(gzip.compress(PAYLOAD, mtime=0))

    def scenario() -> bytes:
        with gzipx.open(path, "rb") as handle:
            return b"".join(iter(lambda: handle.read(512), b""))

    assert scenario() == PAYLOAD
    total = opened[-1].reads
    assert total > 1
    for fail_at in range(1, total + 1):
        plan.update(read=fail_at)
        with pytest.raises(_FaultError) as caught:
            scenario()
        assert caught.value is opened[-1].fault, fail_at
        assert opened[-1].closed, fail_at


# ── early closure ────────────────────────────────────────────────────────────


def _descriptors() -> int:
    assert _FD_DIR is not None
    return sum(path.name.isdecimal() for path in Path(_FD_DIR).iterdir())


@pytest.fixture
def gz_path(tmp_path) -> Path:
    path = tmp_path / "rows.gz"
    path.write_bytes(gzip.compress(PAYLOAD * 4, mtime=0))
    return path


@pytest.mark.skipif(_FD_DIR is None, reason="needs /proc/self/fd or /dev/fd")
@pytest.mark.parametrize("mode", ["rb", "rt"])
def test_closing_a_partly_read_file_releases_its_descriptor(gz_path, mode) -> None:
    before = _descriptors()
    handle = gzipx.open(gz_path, mode, encoding="latin-1" if mode == "rt" else None)
    first = next(iter(handle))
    assert first in (b"row of text\n", "row of text\n")
    assert _descriptors() == before + 1
    handle.close()
    assert handle.closed
    assert _descriptors() == before


@pytest.mark.skipif(_FD_DIR is None, reason="needs /proc/self/fd or /dev/fd")
def test_failed_text_wrapper_setup_releases_the_binary_file(gz_path) -> None:
    before = _descriptors()
    with pytest.raises(LookupError):
        gzipx.open(gz_path, "rt", encoding="no-such-codec")
    assert _descriptors() == before


def test_failed_text_wrapper_setup_closes_without_waiting_for_collection(
    monkeypatch, gz_path
) -> None:
    # Base installs bind stdlib gzip.open exactly, which leaves this to collection.
    require_backend("zlib_ng")
    created: list[gzipx.GzipFile] = []

    class Kept(gzipx.GzipFile):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setitem(gzipx.open.__globals__, "GzipFile", Kept)
    with pytest.raises(LookupError):
        gzipx.open(gz_path, "rt", encoding="no-such-codec")
    assert len(created) == 1
    assert created[0].closed
    assert created[0].myfileobj is None


@pytest.mark.skipif(_FD_DIR is None, reason="needs /proc/self/fd or /dev/fd")
def test_corrupt_owned_file_is_released_after_the_read_error(tmp_path) -> None:
    path = tmp_path / "corrupt.gz"
    path.write_bytes(gzip.compress(PAYLOAD, mtime=0)[:-4])
    before = _descriptors()
    with pytest.raises(EOFError), gzipx.open(path, "rb") as handle:
        handle.read()
    assert handle.closed
    assert _descriptors() == before


def test_early_close_leaves_a_caller_stream_open_and_positioned_for_reuse() -> None:
    source = io.BytesIO(gzip.compress(PAYLOAD, mtime=0))
    handle = gzipx.GzipFile(fileobj=source)
    assert handle.read(5) == PAYLOAD[:5]
    handle.close()
    assert not source.closed
    source.seek(0)
    assert gzipx.GzipFile(fileobj=source).read() == PAYLOAD
