"""A renamed directory must not redirect the default walk outside its tree.

The confirmed defect queued path strings after a no-follow entry check, then
passed those names to ``scandir`` later. These schedules replace a directory at
that later open boundary. All files, directories, and links are fixture-owned.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any

import pytest

from unstd import fs


_DESCRIPTOR_WALK = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.scandir in os.supports_fd
)


@pytest.mark.skipif(not _DESCRIPTOR_WALK, reason="POSIX descriptor-relative opens")
def test_queued_directory_replaced_with_symlink_is_not_descended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    queued = root / "queued"
    queued.mkdir(parents=True)
    visible = root / "visible"
    visible.write_text("permitted")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("must not be enumerated")
    replacements: list[Path] = []

    def replace_queued() -> None:
        if not replacements:
            queued.rmdir()
            queued.symlink_to(outside, target_is_directory=True)
            replacements.append(queued)

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Any, *args: Any, **kwargs: Any) -> int:
            if str(path) == "queued" and kwargs.get("dir_fd") is not None:
                replace_queued()
            return os.open(path, *args, **kwargs)

        def scandir(self, path: Any) -> os.ScandirIterator[str]:
            if not isinstance(path, int) and Path(path) == queued:
                replace_queued()
            return os.scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        assert list(fs.walk_files(root)) == [visible]
    assert replacements == [queued]
    assert secret.read_text() == "must not be enumerated"


@pytest.mark.skipif(not _DESCRIPTOR_WALK, reason="POSIX descriptor-relative opens")
@pytest.mark.parametrize("replace_root", [False, True], ids=["ancestor", "root"])
def test_ancestor_replacement_cannot_redirect_an_open_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_root: bool
) -> None:
    root = tmp_path / "root"
    child = root / "parent" / "child"
    child.mkdir(parents=True)
    permitted = child / "permitted"
    permitted.write_text("original tree")
    outside = tmp_path / "outside"
    outside_child = outside / "parent" / "child" if replace_root else outside / "child"
    outside_child.mkdir(parents=True)
    secret = outside_child / "secret"
    secret.write_text("outside tree")
    replaced = root if replace_root else root / "parent"
    moved = tmp_path / "original-directory"
    replacements: list[Path] = []

    def replace_ancestor() -> None:
        if not replacements:
            replaced.rename(moved)
            replaced.symlink_to(outside, target_is_directory=True)
            replacements.append(replaced)

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Any, *args: Any, **kwargs: Any) -> int:
            if str(path) == "child" and kwargs.get("dir_fd") is not None:
                replace_ancestor()
            return os.open(path, *args, **kwargs)

        def scandir(self, path: Any) -> os.ScandirIterator[str]:
            if not isinstance(path, int) and Path(path) == child:
                replace_ancestor()
            return os.scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        assert list(fs.walk_files(root)) == [permitted]
    assert replacements == [replaced]
    assert secret.read_text() == "outside tree"
    # Enumeration was anchored to the original directory. Returned Paths are
    # lexical names, so a later open is deliberately not claimed to be pinned.
    original_child = moved / "parent" / "child" if replace_root else moved / "child"
    assert (original_child / "permitted").read_text() == "original tree"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
@pytest.mark.parametrize("suffix", ["", "/"], ids=["plain", "trailing-slash"])
def test_default_walk_does_not_follow_a_symlink_root(
    tmp_path: Path, suffix: str
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    expected = real / "visible"
    expected.write_text("permitted")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert list(fs.walk_files(real)) == [expected]
    assert list(fs.walk_files(str(link) + suffix)) == []
    assert list(fs.walk_files(link, follow_symlinks=True)) == [link / "visible"]


@pytest.mark.skipif(not _DESCRIPTOR_WALK, reason="POSIX descriptor-relative opens")
@pytest.mark.parametrize("finish", ["exhaust", "close", "throw"])
def test_lazy_walk_releases_all_open_ancestor_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    leaf = tmp_path / "one" / "two" / "three"
    leaf.mkdir(parents=True)
    expected = leaf / "visible"
    expected.write_text("permitted")
    acquired: list[int] = []
    released: list[int] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, *args: Any, **kwargs: Any) -> int:
            fd = os.open(*args, **kwargs)
            acquired.append(fd)
            return fd

        def close(self, fd: int) -> None:
            released.append(fd)
            os.close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        walker = fs.walk_files(tmp_path)
        assert acquired == []  # constructing the generator performs no I/O
        assert next(walker) == expected
        assert len(acquired) == 4
        assert released == []
        for fd in acquired:
            assert not os.get_inheritable(fd)
            assert os.fstat(fd)
        if finish == "exhaust":
            assert list(walker) == []
        elif finish == "close":
            walker.close()
        else:
            failure = OSError("consumer cancelled traversal")
            with pytest.raises(OSError, match="consumer cancelled") as caught:
                walker.throw(failure)
            assert caught.value is failure
    assert sorted(released) == sorted(acquired)
    for fd in acquired:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)
    assert list(fs.walk_files(tmp_path)) == [expected]


@pytest.mark.skipif(not _DESCRIPTOR_WALK, reason="POSIX descriptor-relative opens")
@pytest.mark.parametrize("failure", [PermissionError("denied"), RuntimeError("fault")])
def test_scandir_setup_failure_releases_the_new_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    expected = tmp_path / "visible"
    expected.write_text("permitted")
    acquired: list[int] = []
    failed: list[int] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, *args: Any, **kwargs: Any) -> int:
            fd = os.open(*args, **kwargs)
            acquired.append(fd)
            return fd

        def scandir(self, fd: int) -> Any:
            failed.append(fd)
            raise failure

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        if isinstance(failure, OSError):
            assert list(fs.walk_files(tmp_path)) == []
        else:
            with pytest.raises(RuntimeError, match="fault") as caught:
                list(fs.walk_files(tmp_path))
            assert caught.value is failure
    assert failed == acquired
    assert len(acquired) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(acquired[0])
    assert list(fs.walk_files(tmp_path)) == [expected]


@pytest.mark.skipif(not _DESCRIPTOR_WALK, reason="POSIX descriptor-relative opens")
def test_wide_walk_does_not_hold_a_descriptor_for_every_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = set()
    for index in range(100):
        directory = tmp_path / str(index)
        directory.mkdir()
        path = directory / "visible"
        path.write_text("permitted")
        expected.add(path)
    active: set[int] = set()
    peak = 0
    opened = 0

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, *args: Any, **kwargs: Any) -> int:
            nonlocal peak, opened
            fd = os.open(*args, **kwargs)
            active.add(fd)
            peak = max(peak, len(active))
            opened += 1
            return fd

        def close(self, fd: int) -> None:
            active.remove(fd)
            os.close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        assert set(fs.walk_files(tmp_path)) == expected
    assert opened == 101
    assert peak <= 2  # the root and one child, regardless of sibling count
    assert active == set()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fixture")
@pytest.mark.parametrize("replace_at", ["identity-check", "scan-open"])
def test_portable_fallback_rechecks_the_queued_directory_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_at: str
) -> None:
    root = tmp_path / "root"
    queued = root / "queued"
    queued.mkdir(parents=True)
    visible = root / "visible"
    visible.write_text("permitted")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("outside tree")
    checks = 0
    replacements: list[Path] = []

    def replace_queued() -> None:
        if not replacements:
            queued.rmdir()
            queued.symlink_to(outside, target_is_directory=True)
            replacements.append(queued)

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def lstat(self, path: Path) -> os.stat_result:
            nonlocal checks
            if path == queued:
                checks += 1
                if replace_at == "identity-check" and checks == 2:
                    replace_queued()
            return os.lstat(path)

        def scandir(self, path: Path) -> os.ScandirIterator[str]:
            if path == queued and replace_at == "scan-open":
                replace_queued()
            return os.scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        patch.setattr(fs, "_FD_WALK_SUPPORTED", False)
        assert list(fs.walk_files(root)) == [visible]
    assert replacements == [queued]
    assert checks >= 2
    assert secret.read_text() == "outside tree"


def test_portable_fallback_preserves_normal_nested_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = tmp_path / "nested" / "visible"
    leaf.parent.mkdir()
    leaf.write_text("permitted")
    with monkeypatch.context() as patch:
        patch.setattr(fs, "_FD_WALK_SUPPORTED", False)
        assert list(fs.walk_files(tmp_path)) == [leaf]
        assert list(fs.walk_files(tmp_path / "missing")) == []


def test_portable_fallback_rejects_directory_reparse_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.lstat(tmp_path)

    class Boundary:
        def lstat(self, path: Path) -> SimpleNamespace:
            assert path == tmp_path
            return SimpleNamespace(
                st_mode=original.st_mode,
                st_dev=original.st_dev,
                st_ino=original.st_ino,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )

        def scandir(self, path: Path) -> Any:
            pytest.fail(f"directory reparse point was scanned: {path}")

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        patch.setattr(fs, "_FD_WALK_SUPPORTED", False)
        assert list(fs.walk_files(tmp_path)) == []


def test_descriptor_walk_preserves_relative_output_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = tmp_path / "tree" / "nested" / "visible"
    leaf.parent.mkdir(parents=True)
    leaf.write_text("permitted")
    monkeypatch.chdir(tmp_path)
    assert list(fs.walk_files("tree")) == [Path("tree/nested/visible")]
