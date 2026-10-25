"""Descriptor-anchored and portable filesystem traversal."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

_Pathish = str | os.PathLike[str]


def _open_walk_dir(
    path: _Pathish, *, os_api: ModuleType, flags: int, parent_fd: int | None = None
) -> tuple[int, Iterator[os.DirEntry[str]], ExitStack] | None:
    """Acquire one no-follow directory and its scan, transferring both owners."""
    try:
        with ExitStack() as owned:
            fd = os_api.open(path, flags, dir_fd=parent_fd)
            owned.callback(os_api.close, fd)
            scan = owned.enter_context(os_api.scandir(fd))
            return fd, scan, owned.pop_all()
    except OSError:
        return None  # vanished, unreadable, or replaced by a symlink


def _walk_descriptors(root: Path, os_api: ModuleType, flags: int) -> Iterator[Path]:
    """Anchor every child open to its parent's handle, with O(depth) owners."""
    opened = _open_walk_dir(root, os_api=os_api, flags=flags)
    if opened is None:
        return
    stack = [(root, *opened)]
    try:
        while stack:
            path, fd, scan, owned = stack[-1]
            for entry in scan:
                child = path / entry.name
                try:
                    directory = entry.is_dir(follow_symlinks=False)
                    regular = not directory and entry.is_file(follow_symlinks=False)
                except OSError:
                    continue  # an entry vanished or became unreadable during its check
                if directory:
                    opened = _open_walk_dir(
                        entry.name, os_api=os_api, flags=flags, parent_fd=fd
                    )
                    if opened is not None:
                        stack.append((child, *opened))
                        break  # resume this anchored scan after its child closes
                elif regular:
                    yield child
            else:
                stack.pop()
                owned.close()
    finally:
        # Close every live frame on exhaustion, generator.close(), or an error.
        # ExitStack still closes the remaining frames if one cleanup raises.
        with ExitStack() as cleanup:
            for _, _, _, owned in stack:
                cleanup.callback(owned.close)


def _directory_identity(path: Path, os_api: ModuleType) -> tuple[int, int] | None:
    """The identity of an ordinary directory, rejecting links/reparse points."""
    try:
        info = os_api.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        return None
    return info.st_dev, info.st_ino


def _walk_portable(root: Path, os_api: ModuleType) -> Iterator[Path]:
    """Best-effort no-follow traversal where directory handles are unavailable."""
    identity = _directory_identity(root, os_api)
    if identity is None:
        return
    stack = [(root, identity)]
    while stack:
        path, expected = stack.pop()
        if _directory_identity(path, os_api) != expected:
            continue
        try:
            scan = os_api.scandir(path)
        except OSError:
            continue
        with scan as entries:
            # Detect replacements both while queued and across the scan open.
            # These path checks cannot close every race on a changing namespace.
            if _directory_identity(path, os_api) != expected:
                continue
            for entry in entries:
                if _directory_identity(path, os_api) != expected:
                    break
                child = path / entry.name
                try:
                    directory = entry.is_dir(follow_symlinks=False)
                    regular = not directory and entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if directory:
                    identity = _directory_identity(child, os_api)
                    if identity is not None:
                        stack.append((child, identity))
                elif regular:
                    yield child


def _walk_following(root: _Pathish, os_api: ModuleType) -> Iterator[Path]:
    """The explicitly opted-in path walk, including symlinked directories."""
    stack: list[_Pathish] = [root]
    while stack:
        try:
            scan = os_api.scandir(stack.pop())
        except OSError:
            continue  # vanished or unreadable directory — skip, don't abort the walk
        with scan as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=True):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=True):
                    yield Path(entry.path)
