"""Nested traversal failures must release every acquired scanner and descriptor."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any, Self

import pytest

from unstd import fs


_DESCRIPTOR_WALK = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.scandir in os.supports_fd
)
pytestmark = pytest.mark.skipif(
    not _DESCRIPTOR_WALK, reason="POSIX descriptor-relative opens"
)


class _Scan:
    def __init__(
        self, scan: os.ScandirIterator[str], name: str, boundary: _Boundary
    ) -> None:
        self.scan = scan
        self.name = name
        self.boundary = boundary
        self.yielded = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> os.DirEntry[str]:
        if self.name == self.boundary.iteration_failure_at and self.yielded:
            self.boundary.failures.append((self.name, "iteration"))
            raise self.boundary.error
        entry = next(self.scan)
        self.yielded = True
        return entry

    def close(self) -> None:
        self.scan.close()
        self.boundary.scans_closed.append(self.name)
        if self.name == self.boundary.close_failure_at:
            self.boundary.failures.append((self.name, "close"))
            raise self.boundary.error


class _Boundary:
    def __init__(
        self,
        error: Exception,
        *,
        iteration_failure_at: str | None = None,
        close_failure_at: str | None = None,
    ) -> None:
        self.error = error
        self.iteration_failure_at = iteration_failure_at
        self.close_failure_at = close_failure_at
        self.opened: dict[int, str] = {}
        self.descriptors_closed: list[int] = []
        self.scans_opened: list[str] = []
        self.scans_closed: list[str] = []
        self.failures: list[tuple[str, str]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def open(self, path: Any, *args: Any, **kwargs: Any) -> int:
        fd = os.open(path, *args, **kwargs)
        self.opened[fd] = Path(path).name
        return fd

    def scandir(self, fd: int) -> _Scan:
        scan = os.scandir(fd)
        name = self.opened[fd]
        self.scans_opened.append(name)
        return _Scan(scan, name, self)

    def close(self, fd: int) -> None:
        os.close(fd)
        self.descriptors_closed.append(fd)


def _tree(tmp_path: Path) -> Path:
    leaf = tmp_path / "tree" / "branch" / "leaf-雪"
    leaf.mkdir(parents=True)
    visible = leaf / "résumé.txt"
    visible.write_text("original tree", encoding="utf-8")
    return visible


def _assert_all_closed(boundary: _Boundary) -> None:
    assert boundary.scans_opened == ["tree", "branch", "leaf-雪"]
    assert sorted(boundary.scans_closed) == sorted(boundary.scans_opened)
    assert sorted(boundary.descriptors_closed) == sorted(boundary.opened)
    for fd in boundary.opened:
        with pytest.raises(OSError) as caught:  # noqa: PT011 — exact errno below
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_nested_iteration_failure_preserves_error_and_releases_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    visible = _tree(tmp_path)
    error = error_type("injected scanner iteration failure")
    boundary = _Boundary(error, iteration_failure_at="leaf-雪")
    monkeypatch.chdir(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)
        walker = fs.walk_files("tree")
        assert boundary.opened == {}
        assert boundary.scans_opened == []
        assert next(walker) == visible.relative_to(tmp_path)
        with pytest.raises(error_type, match="injected scanner iteration") as caught:
            list(walker)
        assert caught.value is error
    assert boundary.failures == [("leaf-雪", "iteration")]
    _assert_all_closed(boundary)
    assert list(fs.walk_files("tree")) == [visible.relative_to(tmp_path)]


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
@pytest.mark.parametrize("close_failure_at", ["leaf-雪", "branch"])
@pytest.mark.parametrize("finish", ["exhaust", "close"])
def test_scanner_close_failure_still_releases_all_ancestor_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    close_failure_at: str,
    finish: str,
) -> None:
    visible = _tree(tmp_path)
    error = error_type("injected scanner close failure")
    boundary = _Boundary(error, close_failure_at=close_failure_at)

    def finish_walk() -> None:
        if finish == "exhaust":
            list(walker)
        else:
            walker.close()

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", boundary)
        walker = fs.walk_files(tmp_path / "tree")
        assert boundary.opened == {}
        assert next(walker) == visible
        with pytest.raises(error_type, match="injected scanner close") as caught:
            finish_walk()
        assert caught.value is error
    assert boundary.failures == [(close_failure_at, "close")]
    _assert_all_closed(boundary)
    assert list(fs.walk_files(tmp_path / "tree")) == [visible]
