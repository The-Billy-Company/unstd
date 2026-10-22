"""Collision, symlink, durability, and traversal controls on fixture-owned paths."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any

import pytest

from unstd import fs


@pytest.mark.parametrize("collision_kind", ["file", "directory", "symlink"])
def test_scratch_collision_cannot_overwrite_existing_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collision_kind: str
) -> None:
    if collision_kind == "symlink" and os.name == "nt":
        pytest.skip("Windows symlink creation requires optional platform privileges")
    dest = tmp_path / "state"
    dest.write_bytes(b"old")
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"protected unrelated contents")
    occupied = tmp_path / ".state.occupied.tmp"
    if collision_kind == "file":
        occupied.write_bytes(b"protected collision")
    elif collision_kind == "directory":
        occupied.mkdir()
    else:
        occupied.symlink_to(unrelated)
    tokens = iter(["occupied", "occupied", "available"])
    seen: list[str] = []

    def token_hex(width: int) -> str:
        assert width == 4
        token = next(tokens)
        seen.append(token)
        return token

    with monkeypatch.context() as patch:
        patch.setattr(fs, "secrets", SimpleNamespace(token_hex=token_hex))
        fs.atomic_write(dest, b"new complete version")
    assert seen == ["occupied", "occupied", "available"]
    assert dest.read_bytes() == b"new complete version"
    assert unrelated.read_bytes() == b"protected unrelated contents"
    assert occupied.exists()
    if collision_kind == "file":
        assert occupied.read_bytes() == b"protected collision"
    elif collision_kind == "symlink":
        assert occupied.is_symlink()
        assert occupied.resolve() == unrelated
    assert not (tmp_path / ".state.available.tmp").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink control")
def test_atomic_write_replaces_destination_symlink_without_touching_referent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"referent must survive")
    dest = tmp_path / "state"
    dest.symlink_to(target)
    fs.atomic_write(dest, b"new destination")
    assert target.read_bytes() == b"referent must survive"
    assert not dest.is_symlink()
    assert dest.read_bytes() == b"new destination"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor inheritance flag")
def test_scratch_descriptor_is_not_inherited_by_child_processes(tmp_path: Path) -> None:
    dest = tmp_path / "state"
    with fs.atomic_writer(dest) as writer:
        assert os.get_inheritable(writer.fileno()) is False
        writer.write("new")
    assert dest.read_text() == "new"


@pytest.mark.parametrize("failure", [None, "open", "fsync", "close"])
def test_sync_dir_is_best_effort_and_closes_acquired_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    if os.name == "nt" and failure != "open":
        pytest.skip(
            "Windows rejects real directory descriptors; open refusal is tested"
        )
    reached: list[str] = []
    descriptors: list[int] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Path, flags: int) -> int:
            reached.append("open")
            if failure == "open":
                raise PermissionError(errno.EACCES, "directory open rejected")
            fd = os.open(path, flags)
            descriptors.append(fd)
            return fd

        def fsync(self, fd: int) -> None:
            reached.append("fsync")
            assert stat.S_ISDIR(os.fstat(fd).st_mode)
            if failure == "fsync":
                raise OSError(errno.EINVAL, "directory sync unsupported")

        def close(self, fd: int) -> None:
            reached.append("close")
            os.close(fd)
            if failure == "close":
                raise OSError(errno.EIO, "directory close failed")

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        fs.sync_dir(tmp_path)
    assert reached == (["open"] if failure == "open" else ["open", "fsync", "close"])
    for descriptor in descriptors:
        with pytest.raises(OSError, match="Bad file descriptor") as closed:
            os.fstat(descriptor)
        assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("operation", ["write", "replace"])
@pytest.mark.skipif(
    os.name != "posix", reason="POSIX directory descriptor sync ordering"
)
def test_durable_commit_orders_file_sync_replace_and_parent_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    src = tmp_path / "source"
    src.write_bytes(b"complete new contents")
    dest = tmp_path / "destination"
    dest.write_bytes(b"old")
    events: list[str] = []
    replace = Path.replace

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def fsync(self, fd: int) -> None:
            kind = (
                "directory_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync"
            )
            events.append(kind)
            os.fsync(fd)

    def track_replace(path: Path, target: Path) -> Path:
        events.append("replace")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        patch.setattr(Path, "replace", track_replace)
        if operation == "write":
            fs.atomic_write(dest, b"complete new contents", durable=True)
        else:
            fs.atomic_replace(src, dest, durable=True)
    assert events == (
        ["file_sync", "replace", "directory_sync"]
        if operation == "write"
        else ["replace", "directory_sync"]
    )
    assert dest.read_bytes() == b"complete new contents"


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX directory descriptor sync failure"
)
def test_parent_sync_failure_happens_after_successful_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "state"
    dest.write_bytes(b"old")
    reached: list[int] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def fsync(self, fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                reached.append(fd)
                assert dest.read_bytes() == b"committed"
                raise OSError(errno.EINVAL, "directory sync unsupported")
            os.fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        fs.atomic_write(dest, b"committed", durable=True)
    assert len(reached) == 1
    assert dest.read_bytes() == b"committed"
    assert list(tmp_path.iterdir()) == [dest]
    with pytest.raises(OSError, match="Bad file descriptor") as closed:
        os.fstat(reached[0])
    assert closed.value.errno == errno.EBADF


def test_atomic_replace_failure_preserves_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "source"
    src.write_bytes(b"new")
    dest = tmp_path / "destination"
    dest.write_bytes(b"old")
    error = PermissionError(errno.EACCES, "replace refused")
    calls: list[tuple[Path, Path]] = []

    def fail_replace(path: Path, target: Path) -> Path:
        calls.append((path, target))
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(PermissionError) as caught:
            fs.atomic_replace(src, dest)
    assert caught.value is error
    assert calls == [(src, dest)]
    assert src.read_bytes() == b"new"
    assert dest.read_bytes() == b"old"
    fs.atomic_replace(src, dest)
    assert dest.read_bytes() == b"new"
    assert not src.exists()


def test_atomic_writer_parent_is_a_file_preserves_existing_file(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_bytes(b"must survive")
    with pytest.raises((FileExistsError, NotADirectoryError)):
        fs.atomic_write(blocker / "child", "not written")
    assert blocker.read_bytes() == b"must survive"
    assert list(tmp_path.iterdir()) == [blocker]


def test_atomic_writer_cannot_replace_existing_directory(tmp_path: Path) -> None:
    destination = tmp_path / "directory"
    destination.mkdir()
    child = destination / "existing"
    child.write_bytes(b"protected")
    with pytest.raises((IsADirectoryError, PermissionError)):
        fs.atomic_write(destination, b"must not replace directory")
    assert destination.is_dir()
    assert child.read_bytes() == b"protected"
    assert sorted(tmp_path.iterdir()) == [destination]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptor ownership")
@pytest.mark.parametrize(
    "error", [RuntimeError("interrupted sync"), KeyboardInterrupt()]
)
def test_sync_dir_propagates_non_os_failure_but_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    descriptors: list[int] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def fsync(self, fd: int) -> None:
            descriptors.append(fd)
            raise error

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        with pytest.raises(type(error)) as caught:
            fs.sync_dir(tmp_path)
    assert caught.value is error
    assert len(descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink controls")
def test_default_walk_never_follows_escape_file_or_directory_or_cycle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    visible = root / "visible"
    visible.write_bytes(b"visible")
    outside = tmp_path / "outside"
    outside.mkdir()
    hidden = outside / "secret"
    hidden.write_bytes(b"outside root")
    (root / "escape-directory").symlink_to(outside, target_is_directory=True)
    (root / "escape-file").symlink_to(hidden)
    (root / "cycle").symlink_to(root, target_is_directory=True)
    assert list(fs.walk_files(root)) == [visible]
    assert hidden.read_bytes() == b"outside root"


def test_walk_skips_inaccessible_subdirectory_and_keeps_accessible_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    denied = tmp_path / "denied"
    denied.mkdir()
    (denied / "secret").write_text("not visited")
    readable = tmp_path / "readable"
    readable.mkdir()
    expected = readable / "visible"
    expected.write_text("visible")
    refused: list[Path] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Any, *args: Any, **kwargs: Any) -> int:
            if str(path) == denied.name and kwargs.get("dir_fd") is not None:
                refused.append(denied)
                raise PermissionError(errno.EACCES, "directory access denied")
            return os.open(path, *args, **kwargs)

        def scandir(self, path: Any) -> os.ScandirIterator[str]:
            if not isinstance(path, int) and Path(path) == denied:
                refused.append(denied)
                raise PermissionError(errno.EACCES, "directory access denied")
            return os.scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        assert list(fs.walk_files(tmp_path)) == [expected]
    assert refused == [denied]


def test_walk_missing_root_is_empty(tmp_path: Path) -> None:
    assert list(fs.walk_files(tmp_path / "does-not-exist")) == []
