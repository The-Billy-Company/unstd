"""Atomic rewrites must not disclose protected data through their scratch file.

The create seam schedules an observer before the writer resumes: permission
tightening cannot revoke an already-open descriptor. The second schedule swaps
the scratch directory entry for a fixture-owned symlink before mode copying;
changing permissions must stay attached to the writer's acquired inode.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING, Any

import pytest

from unstd import fs


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


def test_protected_rewrite_never_exposes_a_readable_scratch_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "credentials"
    dest.write_bytes(b"previous secret")
    dest.chmod(0o600)
    observers: list[int] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Path, flags: int, mode: int) -> int:
            fd = os.open(path, flags, mode)
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o004:
                # Another user could acquire this handle before chmod; the OS
                # retains its read access when the writer tightens permissions.
                observers.append(os.open(path, os.O_RDONLY))
            return fd

    prior_umask = os.umask(0o022)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(fs, "os", Boundary())
            fs.atomic_write(dest, b"replacement secret")
        leaked = [os.read(fd, 100) for fd in observers]
    finally:
        os.umask(prior_umask)
        for fd in observers:
            os.close(fd)

    assert leaked == []
    assert dest.read_bytes() == b"replacement secret"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [dest]


def test_scratch_replacement_cannot_chmod_an_unrelated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "shared-config"
    dest.write_bytes(b"previous config")
    dest.chmod(0o644)
    unrelated = tmp_path / "private-key"
    unrelated.write_bytes(b"unrelated secret")
    unrelated.chmod(0o600)
    replaced: list[Path] = []

    class Boundary:
        def __getattr__(self, name: str) -> Any:
            return getattr(os, name)

        def open(self, path: Path, flags: int, mode: int) -> int:
            fd = os.open(path, flags, mode)
            path.unlink()
            path.symlink_to(unrelated)
            replaced.append(path)
            return fd

    def write_then_fail() -> None:
        with fs.atomic_writer(dest) as writer:
            writer.write("discarded config")
            message = "cancel rewrite"
            raise RuntimeError(message)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "os", Boundary())
        with pytest.raises(RuntimeError, match="cancel rewrite"):
            write_then_fail()

    assert len(replaced) == 1
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o600
    assert unrelated.read_bytes() == b"unrelated secret"
    assert dest.read_bytes() == b"previous config"
    assert set(tmp_path.iterdir()) == {dest, unrelated}
