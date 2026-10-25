"""Directory synchronization with explicit operating-system dependencies."""

from __future__ import annotations

from contextlib import suppress
import errno
import os
import stat
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from types import ModuleType


_Pathish = str | os.PathLike[str]


def _sync_directory_handle(
    fd: int, path: _Pathish, *, strict: bool, os_api: ModuleType
) -> None:
    """Validate strict handle ownership before asking the OS to persist it."""
    if strict and not stat.S_ISDIR(os_api.fstat(fd).st_mode):
        raise NotADirectoryError(errno.ENOTDIR, os_api.strerror(errno.ENOTDIR), path)
    os_api.fsync(fd)


def sync_directory(path: _Pathish, *, strict: bool, os_api: ModuleType) -> None:
    """Sync a directory, suppressing OS failures unless ``strict=True``.

    Strict mode requires a directory handle and reports open, fsync and close
    failures, including platforms that do not support directory syncing. This
    observes OS acknowledgments; it cannot establish physical power-loss safety
    independently of the filesystem and storage device.
    """
    try:
        flags = os_api.O_RDONLY | (getattr(os, "O_DIRECTORY", 0) if strict else 0)
        fd = os_api.open(path, flags)
    except OSError:
        if strict:
            raise
        return  # e.g. Windows cannot open a directory as an ordinary file
    try:
        _sync_directory_handle(fd, path, strict=strict, os_api=os_api)
    except BaseException as error:
        try:
            os_api.close(fd)
        except OSError as cleanup_error:
            if strict:
                error.add_note(f"directory close failed: {cleanup_error}")
        if strict or not isinstance(error, OSError):
            raise
    else:
        if strict:
            os_api.close(fd)
            return
        with suppress(OSError):
            os_api.close(fd)
