"""Adversarial tests for ``unstd.fs`` — the filesystem operations pathlib lacks.

These pin the contract the module exists to guarantee: an atomic write leaves NO
partial file when the write fails mid-flight (temp cleaned up, destination byte-
for-byte unchanged), content round-trips for both text and bytes, the scandir
walk finds nested files and refuses to traverse symlinks by default, and the
directory helpers are idempotent. Assertions prove the *durability/atomicity*
property, not just "a file appeared" — never weaken them to a smoke test.
"""

from __future__ import annotations

import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from unstd import fs


if TYPE_CHECKING:
    from pathlib import Path


# ── atomic_write / atomic_writer: round-trip + all-or-nothing ──────────────────


def test_atomic_write_text_round_trips(tmp_path: Path) -> None:
    """Test atomic write text round trips."""
    dest = tmp_path / "note.txt"
    payload = "héllo — unicode ☃\n"
    assert fs.atomic_write(dest, payload) == dest
    assert dest.read_text(encoding="utf-8") == payload


def test_atomic_write_bytes_round_trips(tmp_path: Path) -> None:
    """Test atomic write bytes round trips."""
    dest = tmp_path / "blob.bin"
    payload = bytes(range(256)) * 8
    fs.atomic_write(dest, payload)
    assert dest.read_bytes() == payload


def test_atomic_write_overwrites_existing_atomically(tmp_path: Path) -> None:
    """Test atomic write overwrites existing atomically."""
    dest = tmp_path / "v.txt"
    fs.atomic_write(dest, "old")
    fs.atomic_write(dest, "new-and-longer")
    assert dest.read_text(encoding="utf-8") == "new-and-longer"


def test_atomic_writer_failure_leaves_no_partial_file(tmp_path: Path) -> None:
    """The core guarantee: a raise mid-write must leave the destination absent (it never existed) and drop no orphan temp file behind."""
    dest = tmp_path / "half.txt"

    def _write_then_crash() -> None:
        with fs.atomic_writer(dest) as fh:
            fh.write("partial data that must never land")
            msg = "boom"
            raise RuntimeError(msg)  # crash before the atomic replace

    with pytest.raises(RuntimeError, match="boom"):
        _write_then_crash()

    assert not dest.exists()  # destination never created
    # no leftover temp file (temp is co-located, named ".half.txt.*.tmp")
    assert list(tmp_path.iterdir()) == []


def test_atomic_writer_failure_preserves_prior_contents(tmp_path: Path) -> None:
    """A failed rewrite of an *existing* file must leave the OLD bytes intact — the reader never observes a torn half-write."""
    dest = tmp_path / "ledger.txt"
    fs.atomic_write(dest, "committed")

    def _rewrite_then_fail() -> None:
        with fs.atomic_writer(dest) as fh:
            fh.write("this should be discarded")
            msg = "rollback"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="rollback"):
        _rewrite_then_fail()

    assert dest.read_text(encoding="utf-8") == "committed"  # unchanged
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.txt"]  # no temp orphan


def test_atomic_write_durable_still_round_trips(tmp_path: Path) -> None:
    """Test atomic write durable still round trips."""
    dest = tmp_path / "d" / "durable.txt"  # nested — also exercises parent mkdir
    fs.atomic_write(dest, "durably written", durable=True)
    assert dest.read_text(encoding="utf-8") == "durably written"


# ── permission preservation: mkstemp's 0o600 must not leak onto dest ───────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_atomic_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    """Regression: ``tempfile.mkstemp`` always creates its scratch file at 0o600
    regardless of umask — rewriting a world-readable file must not silently
    drop it to owner-only.
    """
    dest = tmp_path / "config.toml"
    fs.atomic_write(dest, "old")
    dest.chmod(0o644)

    fs.atomic_write(dest, "new, rewritten")

    assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    assert dest.read_text(encoding="utf-8") == "new, rewritten"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_atomic_write_new_file_respects_umask(tmp_path: Path) -> None:
    """A brand-new destination gets the umask-respecting mode a plain ``open()``
    would produce, not ``mkstemp``'s ``0o600`` scratch-file default.
    """
    old_umask = os.umask(0o022)
    try:
        dest = tmp_path / "fresh.txt"
        fs.atomic_write(dest, "hello")
        assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    finally:
        os.umask(old_umask)


def test_atomic_replace_moves_existing_file(tmp_path: Path) -> None:
    """Test atomic replace moves existing file."""
    src = fs.atomic_write(tmp_path / "src.txt", "payload")
    dest = tmp_path / "dest.txt"
    assert fs.atomic_replace(src, dest) == dest
    assert dest.read_text(encoding="utf-8") == "payload"
    assert not src.exists()


# ── ensure_dir: idempotent ─────────────────────────────────────────────────────


def test_ensure_dir_is_idempotent_and_makes_parents(tmp_path: Path) -> None:
    """Test ensure dir is idempotent and makes parents."""
    target = tmp_path / "a" / "b" / "c"
    assert fs.ensure_dir(target) == target
    assert target.is_dir()
    # second call on an existing tree is a no-op, not an error
    assert fs.ensure_dir(target) == target
    assert target.is_dir()


# ── walk_files: scandir walking, symlink safety ─────────────────────


def test_walk_files_finds_nested_files(tmp_path: Path) -> None:
    """Test walk files finds nested files."""
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    fs.atomic_write(tmp_path / "root.txt", "r")
    fs.atomic_write(tmp_path / "sub" / "mid.txt", "m")
    fs.atomic_write(tmp_path / "sub" / "deep" / "leaf.txt", "l")

    found = {p.relative_to(tmp_path).as_posix() for p in fs.walk_files(tmp_path)}
    assert found == {"root.txt", "sub/mid.txt", "sub/deep/leaf.txt"}


def test_walk_files_honors_follow_symlinks_false(tmp_path: Path) -> None:
    """Test walk files honors follow symlinks false."""
    real = tmp_path / "real"
    real.mkdir()
    fs.atomic_write(real / "inside.txt", "x")

    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    # default: the symlinked directory is not descended
    default = {p.relative_to(tmp_path).as_posix() for p in fs.walk_files(tmp_path)}
    assert default == {"real/inside.txt"}

    # opt-in: following the symlink surfaces the same file through the link
    followed = {
        p.relative_to(tmp_path).as_posix()
        for p in fs.walk_files(tmp_path, follow_symlinks=True)
    }
    assert followed == {"real/inside.txt", "link/inside.txt"}
