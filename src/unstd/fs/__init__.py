"""Filesystem *operations* the stdlib ``pathlib``/``os`` leave to the caller.

Pure stdlib — **no optional extra**. ``pathlib`` is excellent path
*algebra* (joins, suffixes, parents); what it lacks is the durable-write and
fast-walk *operations* every service otherwise re-rolls by hand. This module is
those operations, and only those — it is **not** a ``pathlib`` replacement, so
keep using ``Path`` for path math and reach here for the filesystem I/O around it.

Provided:

- :func:`atomic_write` / :func:`atomic_writer` — the durable-write idiom
  ``pathlib`` lacks: write to a temp file in the *same directory*, ``flush`` +
  ``os.fsync``, then ``os.replace`` (an atomic same-filesystem rename). A reader
  ever sees the whole old file or the whole new file, never a torn half-write,
  and a crash mid-write leaves the destination untouched. Handles ``str`` and
  ``bytes``. For full crash durability the *parent directory* must also be
  fsync'd so the rename entry itself survives power loss (:func:`sync_dir`,
  folded in via ``durable=True``) — the "fsync the directory too" nuance below.
- :func:`ensure_dir` — ``mkdir(parents=True, exist_ok=True)`` as one idempotent call.
- :func:`walk_files` — an ``os.scandir`` walk that reuses the ``DirEntry`` stat
  cache, so it beats ``os.walk`` / ``Path.rglob`` (which re-``stat`` each entry).
  Yields ``pathlib.Path`` for interop with the rest of the codebase.
- :func:`atomic_replace` — atomic ``os.replace`` of an existing file onto a
  destination, with the same optional directory-fsync durability.

Reads are ``Path.read_bytes`` / ``Path.read_text`` and a one-level listing is
``Path.iterdir``: CPython already sizes a whole-file read from ``fstat`` and lists
through ``scandir``, so a wrapper here would only add a frame.

References:
    - **PEP 471** — ``os.scandir`` (Ben Hoyt, 2014). ``DirEntry`` caches the
      ``stat`` result from the directory read, so ``is_dir()`` / ``is_file()`` avoid
      a per-entry syscall — the reason a scandir walk beats ``os.walk`` / ``rglob``.
    - **The atomic-write idiom** — temp-file + ``fsync`` + atomic ``rename`` — as
      used by SQLite's rollback/WAL commit, Postgres, and CPython's own
      ``importlib`` bytecode-cache writer. The *"fsync the parent directory too"*
      durability nuance (an ``os.replace`` is atomic but not persisted until the
      containing directory is synced) is documented in LWN's "Ensuring data reaches
      disk" and the SQLite atomic-commit docs.

Import the module, not its members::

    from unstd import fs

    fs.atomic_write(path, data)

"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
import os
from pathlib import Path
import secrets
import stat
from typing import IO, TYPE_CHECKING, Literal, overload


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


__all__ = [
    "atomic_replace",
    "atomic_write",
    "atomic_writer",
    "ensure_dir",
    "sync_dir",
    "walk_files",
]

_Pathish = str | os.PathLike[str]


def ensure_dir(path: _Pathish) -> Path:
    """``mkdir(parents=True, exist_ok=True)`` folded into one call — create *path* and any missing parents, returning it. Idempotent: a no-op (never an error) when the directory already exists."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sync_dir(path: _Pathish) -> None:
    """``fsync`` a *directory* handle so an entry change inside it (a create or rename) is persisted. The nuance an ``os.replace`` alone misses: the rename is atomic, but not durable until the containing directory is synced. Failure to fsync a directory handle is swallowed (some platforms, notably Windows, reject it) — best-effort durability, never a hard error."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # e.g. Windows can't fsync a directory handle — best effort
    finally:
        os.close(fd)


# Exclusive create, no fd leak into children; O_BINARY/O_NOINHERIT exist only on Windows.
_TMP_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOINHERIT", 0)
)


def _scratch(dest: Path) -> tuple[int, Path]:
    """Create a fresh temp file beside *dest*, at the mode a plain ``open()`` would give it.

    ``tempfile.mkstemp`` hard-codes ``0o600``, and recovering the umask to undo
    that means flipping the process-wide umask, which races every thread
    creating a file in the meantime. Opening at ``0o666`` lets the kernel apply
    the umask instead. An existing destination's own mode is then copied over,
    so rewriting a shared config never drops its other readers to owner-only.
    """
    while True:
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
        try:
            fd = os.open(tmp, _TMP_FLAGS, 0o666)
        except FileExistsError:
            continue
        break
    try:
        os.chmod(tmp, stat.S_IMODE(dest.stat().st_mode))
    except FileNotFoundError:
        pass
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    return fd, tmp


@contextmanager
def _atomic_writer(
    dest: Path, mode: Literal["w", "wb"], encoding: str, durable: bool
) -> Generator[IO[str] | IO[bytes]]:
    ensure_dir(dest.parent)
    fd, tmp_path = _scratch(dest)
    try:
        with os.fdopen(fd, mode, encoding=None if mode == "wb" else encoding) as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if durable:
        sync_dir(dest.parent)


@overload
def atomic_writer(
    path: _Pathish,
    *,
    mode: Literal["w"] = "w",
    encoding: str = "utf-8",
    durable: bool = False,
) -> AbstractContextManager[IO[str]]: ...
@overload
def atomic_writer(
    path: _Pathish,
    *,
    mode: Literal["wb"],
    encoding: str = "utf-8",
    durable: bool = False,
) -> AbstractContextManager[IO[bytes]]: ...
def atomic_writer(
    path: _Pathish,
    *,
    mode: Literal["w", "wb"] = "w",
    encoding: str = "utf-8",
    durable: bool = False,
) -> AbstractContextManager[IO[str] | IO[bytes]]:
    """Yield a handle to a fresh temp file in *path*'s own directory; on clean exit it is flushed, ``os.fsync``'d, and ``os.replace``'d onto *path* (an atomic same-filesystem rename). If the body raises, the temp file is removed and *path* is left exactly as it was — no partial write is ever observable.

    ``mode`` is ``"w"`` (text, *encoding* applied) or ``"wb"`` (binary).
    ``durable=True`` also fsyncs the parent directory so the rename survives a
    power loss. The temp file is co-located with the destination so the rename
    stays on one filesystem (``os.replace`` is only atomic within a filesystem).
    """
    return _atomic_writer(Path(path), mode, encoding, durable)


def atomic_write(
    path: _Pathish,
    data: str | bytes,
    *,
    encoding: str = "utf-8",
    durable: bool = False,
) -> Path:
    """Atomically write *data* to *path* (temp-file + fsync + ``os.replace``).

    ``str`` is encoded with *encoding*; ``bytes`` is written verbatim. A
    concurrent reader sees either the prior file or the complete new file, never
    a torn write, and a crash mid-write discards the temp file leaving *path*
    untouched. ``durable=True`` fsyncs the parent directory too. Returns *path*.
    """
    dest = Path(path)
    if isinstance(data, bytes):
        with atomic_writer(dest, mode="wb", durable=durable) as fh:
            fh.write(data)
    else:
        with atomic_writer(dest, mode="w", encoding=encoding, durable=durable) as fh:
            fh.write(data)
    return dest


def atomic_replace(src: _Pathish, dest: _Pathish, *, durable: bool = False) -> Path:
    """Atomically move *src* onto *dest* via ``os.replace`` — a same-filesystem rename that overwrites *dest* if it exists, with no window where *dest* is absent. ``durable=True`` fsyncs *dest*'s parent so the rename persists.

    Returns *dest*.
    """
    target = Path(dest)
    Path(src).replace(target)
    if durable:
        sync_dir(target.parent)
    return target


def walk_files(root: _Pathish, *, follow_symlinks: bool = False) -> Iterator[Path]:
    """Recursively yield every *file* under *root* as ``Path``, via ``os.scandir``.

    Reuses each ``DirEntry``'s cached ``stat`` (PEP 471) so it beats ``os.walk``
    / ``Path.rglob``, which re-``stat`` every entry. Symlinked directories are
    **not** descended and symlinked files are **not** yielded unless
    ``follow_symlinks=True`` — the safe default avoids symlink cycles and escapes
    out of *root*. Unreadable subdirectories are skipped rather than raising.
    """
    stack: list[_Pathish] = [root]
    while stack:
        try:
            scan = os.scandir(stack.pop())
        except OSError:
            continue  # vanished or unreadable directory — skip, don't abort the walk
        with scan as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=follow_symlinks):
                    yield Path(entry.path)
