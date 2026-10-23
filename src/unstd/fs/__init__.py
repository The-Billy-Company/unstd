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
  ``bytes``. Persisting the rename also requires parent-directory syncing.
  ``durable=True`` attempts it with best-effort errors; ``durable="strict"``
  requires OS acknowledgments, including for new parent entries. Physical
  persistence still depends on the filesystem and device.
- :func:`ensure_dir` — ``mkdir(parents=True, exist_ok=True)`` as one idempotent call.
- :func:`walk_files` — an ``os.scandir`` walk that reuses ``DirEntry`` type
  information and anchors no-follow traversal to directory handles where
  supported. Yields ``pathlib.Path`` for interop with the rest of the codebase.
- :func:`atomic_replace` — atomic ``os.replace`` of an existing file onto a
  destination, with the same optional directory-fsync durability.

Reads are ``Path.read_bytes`` / ``Path.read_text`` and a one-level listing is
``Path.iterdir``: CPython already sizes a whole-file read from ``fstat`` and lists
through ``scandir``, so a wrapper here would only add a frame.

References:
    - **PEP 471** — ``os.scandir`` (Ben Hoyt, 2014). ``DirEntry`` caches the
      ``stat`` result from the directory read, so ``is_dir()`` / ``is_file()`` can
      avoid a per-entry syscall on filesystems that supply entry type information.
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
import errno
import os
from pathlib import Path
import secrets
import stat
from typing import IO, TYPE_CHECKING, Literal, overload

from unstd.fs._internal.durability import sync_directory as _sync_directory
from unstd.fs._internal.walk import _walk_descriptors, _walk_following, _walk_portable


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
type _Durability = bool | Literal["strict"]


def ensure_dir(path: _Pathish) -> Path:
    """``mkdir(parents=True, exist_ok=True)`` folded into one call — create *path* and any missing parents, returning it. Idempotent: a no-op (never an error) when the directory already exists."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sync_dir(path: _Pathish, *, strict: bool = False) -> None:
    """Sync a directory, suppressing OS failures unless ``strict=True``.

    Strict mode requires a directory handle and reports open, fsync and close
    failures, including platforms that do not support directory syncing. This
    observes OS acknowledgments; it cannot establish physical power-loss safety
    independently of the filesystem and storage device.
    """
    _sync_directory(path, strict=strict, os_api=os)


def _prepare_parent(parent: Path, *, strict: bool) -> None:
    """Prepare parents and acknowledge newly created directory entries in order."""
    if not strict:
        ensure_dir(parent)
        return
    missing = []
    current = parent
    while True:
        try:
            info = current.stat()
        except FileNotFoundError:
            missing.append(current)
            current = current.parent
        else:
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(
                    errno.ENOTDIR, os.strerror(errno.ENOTDIR), current
                )
            break
    ensure_dir(parent)
    for created in reversed(missing):
        sync_dir(created.parent, strict=True)
    # Refuse unsupported directory syncing before replacing the destination.
    sync_dir(parent, strict=True)


def _sync_committed(parent: Path, *, strict: bool) -> None:
    """Report post-commit sync failures without implying a successful rollback."""
    try:
        if strict:
            sync_dir(parent, strict=True)
        else:
            sync_dir(parent)
    except BaseException as error:
        error.add_note("replacement already committed; directory sync failed")
        raise


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
    """Create a fresh temp file beside *dest*, preserving its existing mode.

    New destinations request owner-only ``0o600``; the kernel may reduce those
    permissions further through the process umask. An existing destination's
    mode limits the scratch from creation: tightening permissions later cannot
    revoke a read descriptor acquired in between. Copying the exact existing
    mode afterward preserves explicit sharing even when the current umask is
    stricter. The writer never reads or changes the process-wide umask.
    """
    try:
        prior_mode = stat.S_IMODE(dest.stat().st_mode)
    except FileNotFoundError:
        prior_mode = None
    while True:
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
        try:
            fd = os.open(tmp, _TMP_FLAGS, 0o600 if prior_mode is None else prior_mode)
        except FileExistsError:
            continue
        break
    try:
        if prior_mode is not None:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, prior_mode)
            else:
                os.chmod(tmp, prior_mode)
    except BaseException as error:
        _close_scratch_fd(fd, error)
        _discard_scratch(tmp, error)
        raise
    return fd, tmp


def _close_scratch_fd(fd: int, error: BaseException) -> None:
    """Release an owned descriptor without hiding the operation's failure."""
    try:
        os.close(fd)
    except OSError as cleanup_error:
        error.add_note(f"scratch descriptor cleanup failed: {cleanup_error}")


def _discard_scratch(path: Path, error: BaseException) -> None:
    """Attempt rollback while retaining the original error and cleanup evidence."""
    try:
        path.unlink(missing_ok=True)
    except OSError as cleanup_error:
        error.add_note(f"scratch file cleanup failed for {path}: {cleanup_error}")


@contextmanager
def _atomic_writer(
    dest: Path, mode: Literal["w", "wb"], encoding: str, durable: _Durability
) -> Generator[IO[str] | IO[bytes]]:
    strict = durable == "strict"
    _prepare_parent(dest.parent, strict=strict)
    fd, tmp_path = _scratch(dest)
    try:
        try:
            # Retain descriptor ownership even if stream construction fails.
            # A failed fdopen may otherwise close it before our rollback runs.
            fh = os.fdopen(
                fd, mode, encoding=None if mode == "wb" else encoding, closefd=False
            )
            try:
                yield fh
                fh.flush()
                os.fsync(fh.fileno())
            except BaseException as error:
                try:
                    fh.close()
                except BaseException as cleanup_error:
                    error.add_note(f"scratch stream cleanup failed: {cleanup_error}")
                raise
            else:
                fh.close()
        except BaseException as error:
            _close_scratch_fd(fd, error)
            raise
        else:
            os.close(fd)
        tmp_path.replace(dest)
    except BaseException as error:
        _discard_scratch(tmp_path, error)
        raise
    if durable:
        _sync_committed(dest.parent, strict=strict)


@overload
def atomic_writer(
    path: _Pathish,
    *,
    mode: Literal["w"] = "w",
    encoding: str = "utf-8",
    durable: _Durability = False,
) -> AbstractContextManager[IO[str]]: ...
@overload
def atomic_writer(
    path: _Pathish,
    *,
    mode: Literal["wb"],
    encoding: str = "utf-8",
    durable: _Durability = False,
) -> AbstractContextManager[IO[bytes]]: ...
def atomic_writer(
    path: _Pathish,
    *,
    mode: Literal["w", "wb"] = "w",
    encoding: str = "utf-8",
    durable: _Durability = False,
) -> AbstractContextManager[IO[str] | IO[bytes]]:
    """Yield a handle to a fresh temp file in *path*'s own directory; on clean exit it is flushed, ``os.fsync``'d, and ``os.replace``'d onto *path* (an atomic same-filesystem rename). If the body raises, the temp file is removed and *path* is left exactly as it was — no partial write is ever observable.

    ``mode`` is ``"w"`` (text, *encoding* applied) or ``"wb"`` (binary).
    New files request owner-only ``0o600`` filtered by the process umask.
    Existing files retain their prior mode, including explicitly shared access.
    ``durable=True`` attempts parent-directory syncing with legacy best-effort
    error handling. ``durable="strict"`` reports every directory-sync failure,
    including syncing newly created parent entries. Unsupported platforms fail
    the preflight before replacement. A later sync failure is reported after
    commit: the destination already contains the new data and is not rolled back.
    These acknowledgments depend on filesystem/device persistence semantics.
    The temp file is co-located with the destination so the rename
    stays on one filesystem (``os.replace`` is only atomic within a filesystem).
    """
    if mode not in ("w", "wb"):
        msg = "mode must be 'w' or 'wb'"
        raise ValueError(msg)
    return _atomic_writer(Path(path), mode, encoding, durable)


def atomic_write(
    path: _Pathish,
    data: str | bytes,
    *,
    encoding: str = "utf-8",
    durable: _Durability = False,
) -> Path:
    """Atomically write *data* to *path* (temp-file + fsync + ``os.replace``).

    ``str`` is encoded with *encoding*; ``bytes`` is written verbatim. A
    concurrent reader sees either the prior file or the complete new file, never
    a torn write. A process killed before replacement leaves *path* untouched
    but may leave its scratch file for later cleanup. New files request ``0o600``
    filtered by the process umask; existing file modes are preserved.
    ``durable=True`` fsyncs
    the parent directory too, with best-effort errors. ``durable="strict"``
    requires directory-sync acknowledgments and propagates failures; a failure
    after replacement leaves the new data committed. Returns *path*.
    """
    dest = Path(path)
    if isinstance(data, bytes):
        with atomic_writer(dest, mode="wb", durable=durable) as fh:
            fh.write(data)
    else:
        with atomic_writer(dest, mode="w", encoding=encoding, durable=durable) as fh:
            fh.write(data)
    return dest


def atomic_replace(
    src: _Pathish, dest: _Pathish, *, durable: _Durability = False
) -> Path:
    """Atomically move *src* onto *dest* with optional directory syncing.

    ``durable=True`` attempts destination-directory syncing, suppressing OS
    failures. ``durable="strict"`` preflights both parents and requires their
    post-rename sync acknowledgments. Later failures leave the rename committed.
    Neither mode flushes the source file's data: the caller must have synced it
    before moving when its content must persist. Returns *dest*.
    """
    target = Path(dest)
    source = Path(src)
    strict = durable == "strict"
    if strict:
        sync_dir(target.parent, strict=True)
        if source.parent != target.parent:
            sync_dir(source.parent, strict=True)
    source.replace(target)
    if durable:
        _sync_committed(target.parent, strict=strict)
        if strict and source.parent != target.parent:
            _sync_committed(source.parent, strict=True)
    return target


_FD_WALK_SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.scandir in os.supports_fd
)
_WALK_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def walk_files(root: _Pathish, *, follow_symlinks: bool = False) -> Iterator[Path]:
    """Recursively yield files under *root* as ``Path``, via ``os.scandir``.

    The default skips symlinked files and directories, including a symlinked
    root. Where descriptor-relative opens and ``O_NOFOLLOW`` are supported,
    each directory is opened relative to its already-open parent: swapping a
    queued directory or an opened ancestor cannot redirect enumeration through
    a symlink. Live handles grow with tree depth and close on exhaustion, error,
    or iterator ``close()``. Unreadable directories are skipped.

    Other platforms, notably Windows, recheck directory identities and reject
    directory reparse points on a best-effort path walk. That fallback cannot
    provide confinement against concurrent namespace changes. Root path
    ancestors are resolved when the initial root is opened on every platform.
    Returned Paths are names, not pinned handles: opening one later can observe
    a replacement. ``follow_symlinks=True`` retains the ordinary path walk and
    may follow links outside *root* or encounter cycles.
    """
    if follow_symlinks:
        yield from _walk_following(root, os)
    elif _FD_WALK_SUPPORTED:
        yield from _walk_descriptors(Path(root), os, _WALK_FLAGS)
    else:
        yield from _walk_portable(Path(root), os)
