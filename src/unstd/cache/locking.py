"""Bounded process locks for cache fills without long SQLite transactions.

Stable lock files must not be unlinked: a replacement inode could admit a second
owner while an earlier process still holds its original lock. Files live in a
private sibling directory, with at most 64 files per canonical database path.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
import errno
from hashlib import sha256
import os
import sqlite3
import sys
import threading
import time
from typing import TYPE_CHECKING


if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path


__all__ = ()

_STRIPES = 64
_registry_lock = threading.RLock()
_active_fds: set[int] = set()


@dataclass(frozen=True, slots=True)
class _Target:
    """One canonical database identity prepared before its hot operations."""

    path: Path
    identity: str
    directory: Path
    database: str


def prepare(path: Path) -> _Target:
    """Resolve aliases and hash a database path once for a cache handle."""
    canonical = path.resolve()
    identity = os.path.normcase(str(canonical))
    return _Target(
        canonical,
        identity,
        canonical.parent / ".unstd-locks",
        sha256(os.fsencode(identity)).hexdigest(),
    )


class _ProducerState(threading.local):
    """Producer identities owned by one thread and process."""

    def __init__(self) -> None:
        """Start each thread with no active producer."""
        self.identities: set[tuple[int, str]] = set()


_producer_state = _ProducerState()


def _identity(path: Path | _Target) -> str:
    """Canonical spelling shared by symlink, relative, and Windows case aliases."""
    if isinstance(path, _Target):
        return path.identity
    return os.path.normcase(str(path.resolve()))


def _stripe(namespace: str, key: str) -> int:
    """A stable lock bucket, with unambiguous UTF-8 length framing."""
    encoded_namespace = namespace.encode("utf-8")
    encoded_key = key.encode("utf-8")
    digest = sha256()
    for component in (encoded_namespace, encoded_key):
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return int.from_bytes(digest.digest()[:8], "big") % _STRIPES


def check_writable(path: Path | _Target) -> None:
    """Reject factory writes to the same database before taking another lock."""
    if (os.getpid(), _identity(path)) in _producer_state.identities:
        message = "a cache factory cannot write to the same database"
        raise RuntimeError(message)


@contextmanager
def producing(path: Path | _Target) -> Iterator[None]:
    """Mark a producer and prevent an inherited producer from publishing."""
    check_writable(path)
    identity = (os.getpid(), _identity(path))
    _producer_state.identities.add(identity)
    try:
        yield
        if os.getpid() != identity[0]:
            message = "a cache producer cannot publish after fork"
            raise RuntimeError(message)
    finally:
        # The child hook replaces its local state. It must not edit the
        # inherited context's state or treat inherited descriptors as its own.
        if os.getpid() == identity[0]:
            _producer_state.identities.discard(identity)


def _before_fork() -> None:
    """Keep descriptor ownership stable while fork copies process state."""
    _registry_lock.acquire()


def _after_fork_parent() -> None:
    """Allow parent threads to register and close descriptors again."""
    _registry_lock.release()


def _after_fork_child() -> None:
    """Drop inherited handles without unlocking the parent's open descriptions."""
    global _registry_lock, _active_fds, _producer_state
    for descriptor in _active_fds:
        # LOCK_UN would act on the parent's shared open file description too.
        # Closing only the child's reference leaves the parent's lock held.
        with suppress(OSError):
            os.close(descriptor)
    _active_fds = set()
    _registry_lock = threading.RLock()
    _producer_state = _ProducerState()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork,
        after_in_parent=_after_fork_parent,
        after_in_child=_after_fork_child,
    )


def _open(path: Path) -> int:
    """Register each opened descriptor before another thread can fork."""
    with _registry_lock:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        _active_fds.add(descriptor)
        return descriptor


def _try_lock(descriptor: int) -> bool:
    """Try one exclusive acquisition, propagating failures other than contention."""
    try:
        if sys.platform == "win32":
            # _locking starts at the current offset and permits a region past
            # EOF, so an empty stable file needs no initialization write.
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise
    return True


def _unlock(descriptor: int) -> None:
    """Release only a lock successfully acquired by this process."""
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _close(descriptor: int) -> None:
    """Close and unregister together so a fork cannot inherit a recycled fd."""
    with _registry_lock:
        try:
            os.close(descriptor)
        finally:
            _active_fds.discard(descriptor)


def _wait_lock(descriptor: int, deadline: float) -> None:
    """Acquire once or poll until the operation's remaining budget expires."""
    while not _try_lock(descriptor):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            message = "cache database is locked (lock wait timed out)"
            raise sqlite3.OperationalError(message)
        time.sleep(min(0.01, remaining))


def _release_all(
    descriptors: list[tuple[int, bool]], primary: BaseException | None
) -> None:
    """Attempt all releases, retaining a primary operation or cleanup error."""
    first = primary
    for descriptor, acquired in reversed(descriptors):
        try:
            try:
                if acquired:
                    _unlock(descriptor)
            finally:
                _close(descriptor)
        except BaseException as error:
            if first is None:
                first = error
            else:
                first.add_note(f"cache lock cleanup failed: {error}")
    if primary is None and first is not None:
        raise first


@contextmanager
def locked(
    path: Path | _Target,
    namespace: str,
    keys: Iterable[str] | None,
    timeout: float,
) -> Iterator[None]:
    """Own sorted lock stripes under one monotonic acquisition deadline.

    ``keys=None`` fences all stripes for namespace clearing. Hash collisions
    serialize some unrelated keys. The shared lock directory and its stable
    files must remain in place while any process uses the database.
    """
    target = path if isinstance(path, _Target) else prepare(path)
    check_writable(target)
    stripes = (
        range(_STRIPES)
        if keys is None
        else sorted({_stripe(namespace, key) for key in keys})
    )
    directory = target.directory
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptors: list[tuple[int, bool]] = []
    owner = os.getpid()
    deadline = time.monotonic() + timeout
    primary = None
    try:
        for stripe in stripes:
            descriptor = _open(directory / f"{target.database}-{stripe:02x}")
            descriptors.append((descriptor, False))
            _wait_lock(descriptor, deadline)
            descriptors[-1] = (descriptor, True)
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        if os.getpid() == owner:
            _release_all(descriptors, primary)
