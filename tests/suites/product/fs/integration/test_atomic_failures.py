"""Public atomic-write rollback against real files and failing OS/stream seams.

Every injected stage records that it was reached. Before-commit failures retain
the exact prior destination (or its absence), release all acquired descriptors,
remove scratch files when possible, and permit a later successful write. Cleanup
failure is observable evidence attached to the original operation exception.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import IO, Any, Self

import pytest

from unstd import fs


class _FailingStream:
    def __init__(self, stream: IO[Any], boundary: _Boundary) -> None:
        self.stream = stream
        self.boundary = boundary

    def __enter__(self) -> Self:
        self.stream.__enter__()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def write(self, value: str | bytes) -> int:
        if self.boundary.stage == "write":
            self.stream.write(value[: max(1, len(value) // 2)])
            self.boundary.fail("write")
        return self.stream.write(value)

    def flush(self) -> None:
        self.stream.flush()
        if self.boundary.stage == "flush":
            self.boundary.fail("flush")

    def fileno(self) -> int:
        return self.stream.fileno()

    def close(self) -> None:
        self.stream.close()
        if self.boundary.stage == "close":
            self.boundary.fail("close")


class _Boundary:
    """A module-local OS facade; pytest and other libraries keep their real OS."""

    def __init__(self, stage: str, error: BaseException) -> None:
        self.stage = stage
        self.error = error
        self.reached: list[str] = []
        self.descriptors: list[int] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def fail(self, stage: str) -> None:
        self.reached.append(stage)
        raise self.error

    def open(self, path: Path, flags: int, mode: int = 0o777) -> int:
        if self.stage == "open":
            self.fail("open")
        descriptor = os.open(path, flags, mode)
        self.descriptors.append(descriptor)
        return descriptor

    def fdopen(self, descriptor: int, *args: Any, **kwargs: Any) -> Any:
        if self.stage == "fdopen":
            self.fail("fdopen")
        return _FailingStream(os.fdopen(descriptor, *args, **kwargs), self)

    def fsync(self, descriptor: int) -> None:
        if self.stage == "fsync":
            self.fail("fsync")
        os.fsync(descriptor)

    def chmod(self, path: Path, mode: int) -> None:
        if self.stage == "chmod":
            self.fail("chmod")
        path.chmod(mode)

    if hasattr(os, "fchmod"):

        def fchmod(self, descriptor: int, mode: int) -> None:
            if self.stage == "chmod":
                self.fail("chmod")
            os.fchmod(descriptor, mode)


_STAGES = ("open", "fdopen", "write", "flush", "fsync", "close", "replace")


@pytest.mark.parametrize("prior", [None, b"previous committed version\x00\xff"])
@pytest.mark.parametrize("stage", _STAGES)
def test_atomic_write_fault_rolls_back_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, prior: bytes | None
) -> None:
    dest = tmp_path / "state.bin"
    if prior is not None:
        dest.write_bytes(prior)
    error = OSError(errno.EIO, f"injected {stage} failure")
    boundary = _Boundary(stage, error)
    replace = Path.replace

    def fail_replace(path: Path, target: Path) -> Path:
        if path.parent == tmp_path and path.name.startswith(".state.bin."):
            boundary.fail("replace")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)
        if stage == "replace":
            patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="injected") as caught:
            fs.atomic_write(dest, b"uncommitted payload" * 64)

    assert caught.value is error
    assert boundary.reached == [stage]
    assert dest.read_bytes() == prior if prior is not None else not dest.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == (
        [dest.name] if prior is not None else []
    )
    for descriptor in boundary.descriptors:
        with pytest.raises(OSError, match="Bad file descriptor") as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    fs.atomic_write(dest, b"successful recovery")
    assert dest.read_bytes() == b"successful recovery"


def test_chmod_failure_closes_descriptor_and_preserves_mode_and_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "state.bin"
    dest.write_bytes(b"committed")
    old_mode = dest.stat().st_mode
    error = PermissionError(errno.EACCES, "injected chmod refusal")
    boundary = _Boundary("chmod", error)
    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)
        with pytest.raises(PermissionError) as caught:
            fs.atomic_write(dest, b"uncommitted")
    assert caught.value is error
    assert boundary.reached == ["chmod"]
    assert dest.read_bytes() == b"committed"
    assert dest.stat().st_mode == old_mode
    assert list(tmp_path.iterdir()) == [dest]
    for descriptor in boundary.descriptors:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptor)
    fs.atomic_write(dest, b"recovered")
    assert dest.read_bytes() == b"recovered"


@pytest.mark.parametrize(
    "body_error", [RuntimeError("write failed"), KeyboardInterrupt()]
)
@pytest.mark.parametrize(
    "cleanup_error",
    [
        OSError(errno.EIO, "injected close failure"),
        RuntimeError("injected close failure"),
    ],
)
def test_body_error_survives_secondary_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body_error: BaseException,
    cleanup_error: BaseException,
) -> None:
    dest = tmp_path / "state.bin"
    dest.write_bytes(b"old")
    boundary = _Boundary("close", cleanup_error)
    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)

        def write_then_fail() -> None:
            with fs.atomic_writer(dest) as writer:
                writer.write("partial")
                raise body_error

        with pytest.raises(type(body_error)) as caught:
            write_then_fail()
    assert caught.value is body_error
    assert boundary.reached == ["close"]
    assert any("close failure" in note for note in body_error.__notes__)
    assert dest.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [dest]


def test_descriptor_close_failure_does_not_mask_original_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "state.bin"
    dest.write_bytes(b"old")
    error = OSError(errno.ENOSPC, "injected write failure")

    class Boundary(_Boundary):
        def close(self, fd: int) -> None:
            os.close(fd)
            self.reached.append("descriptor_close")
            raise OSError(errno.EIO, "descriptor close failed")

    boundary = Boundary("write", error)
    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)
        with pytest.raises(OSError, match="injected write failure") as caught:
            fs.atomic_write(dest, b"partial payload")
    assert caught.value is error
    assert boundary.reached == ["write", "descriptor_close"]
    assert any("descriptor close failed" in note for note in error.__notes__)
    for fd in boundary.descriptors:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)
    assert dest.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [dest]
    fs.atomic_write(dest, b"recovered")
    assert dest.read_bytes() == b"recovered"


@pytest.mark.parametrize("stage", ["write", "chmod"])
def test_unlink_failure_retains_original_error_and_identifies_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    dest = tmp_path / "state.bin"
    dest.write_bytes(b"old")
    error = OSError(errno.EIO, f"injected {stage} failure")
    boundary = _Boundary(stage, error)
    unlink = Path.unlink
    cleanup_paths: list[Path] = []

    def fail_scratch_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.parent == tmp_path and path.name.startswith(".state.bin."):
            cleanup_paths.append(path)
            raise PermissionError(errno.EACCES, "injected unlink refusal")
        unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)
        patch.setattr(Path, "unlink", fail_scratch_unlink)
        with pytest.raises(OSError, match="injected") as caught:
            fs.atomic_write(dest, b"uncommitted")
    assert caught.value is error
    assert boundary.reached == [stage]
    assert len(cleanup_paths) == 1
    assert cleanup_paths[0].is_file()
    assert any(str(cleanup_paths[0]) in note for note in error.__notes__)
    assert any("unlink refusal" in note for note in error.__notes__)
    assert dest.read_bytes() == b"old"
    for descriptor in boundary.descriptors:
        with pytest.raises(OSError, match="Bad file descriptor") as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF
    cleanup_paths[0].unlink()
    fs.atomic_write(dest, b"recovered")
    assert dest.read_bytes() == b"recovered"
    assert list(tmp_path.iterdir()) == [dest]


def test_invalid_text_encoding_releases_unwrapped_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "state.txt"
    dest.write_text("old")
    descriptors: list[int] = []
    real_open = os.open

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Path, flags: int, mode: int) -> int:
            fd = real_open(path, flags, mode)
            descriptors.append(fd)
            return fd

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        with pytest.raises(LookupError, match="unknown encoding"):
            fs.atomic_write(dest, "new", encoding="not-an-encoding")
    assert len(descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor") as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF
    assert dest.read_text() == "old"
    assert list(tmp_path.iterdir()) == [dest]


def test_encoding_failure_rolls_back_real_stream(tmp_path: Path) -> None:
    dest = tmp_path / "state.txt"
    dest.write_text("old")
    with pytest.raises(UnicodeEncodeError):
        fs.atomic_write(dest, "new ☃", encoding="ascii")
    assert dest.read_text() == "old"
    assert list(tmp_path.iterdir()) == [dest]
    fs.atomic_write(dest, "new ☃")
    assert dest.read_text(encoding="utf-8") == "new ☃"
