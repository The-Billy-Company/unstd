"""Independent lock ownership, bounded resources, wait budgets, and fork cleanup."""

from __future__ import annotations

import errno
import os
import sqlite3
import stat
from threading import Thread
from types import SimpleNamespace

import pytest

from tests.support.environment.process import run_python
from unstd.cache import locking


def test_windows_adapter_uses_nonblocking_byte_lock_and_explicit_unlock(monkeypatch):
    operations = []
    backend = SimpleNamespace(
        LK_NBLCK=2,
        LK_UNLCK=0,
        locking=lambda *arguments: operations.append(("lock", *arguments)),
    )
    monkeypatch.setattr(locking, "msvcrt", backend, raising=False)
    monkeypatch.setattr(locking.sys, "platform", "win32")
    monkeypatch.setattr(
        locking.os, "lseek", lambda *arguments: operations.append(("seek", *arguments))
    )
    assert locking._try_lock(17) is True
    locking._unlock(17)
    assert operations == [
        ("seek", 17, 0, os.SEEK_SET),
        ("lock", 17, backend.LK_NBLCK, 1),
        ("seek", 17, 0, os.SEEK_SET),
        ("lock", 17, backend.LK_UNLCK, 1),
    ]


@pytest.mark.parametrize("number", [errno.EACCES, errno.EAGAIN, errno.EIO])
def test_windows_adapter_retries_only_contention(monkeypatch, number):
    def fail(*arguments):
        message = "injected Windows locking error"
        raise OSError(number, message)

    backend = SimpleNamespace(LK_NBLCK=2, locking=fail)
    monkeypatch.setattr(locking, "msvcrt", backend, raising=False)
    monkeypatch.setattr(locking.sys, "platform", "win32")
    monkeypatch.setattr(locking.os, "lseek", lambda *arguments: 0)
    if number in (errno.EACCES, errno.EAGAIN):
        assert locking._try_lock(17) is False
    else:
        with pytest.raises(OSError, match="injected Windows locking error"):
            locking._try_lock(17)


def test_independent_keys_progress_while_same_key_is_locked(tmp_path):
    path = tmp_path / "cache.sqlite3"
    first = "one"
    second = next(
        str(index)
        for index in range(1_000)
        if locking._stripe("default", str(index)) != locking._stripe("default", first)
    )
    with locking.locked(path, "default", [first], 0):
        with (
            pytest.raises(sqlite3.OperationalError, match="locked"),
            locking.locked(path, "default", [first], 0),
        ):
            pytest.fail("one stripe admitted a second independent owner")
        with locking.locked(path, "default", [second], 0):
            pass
    with locking.locked(path, "default", [first], 0):
        pass


def test_many_keys_create_only_bounded_private_stable_files(tmp_path):
    path = tmp_path / "cache.sqlite3"
    with locking.locked(path, "name\x00space", None, 0):
        assert len(locking._active_fds) == 64
    directory = tmp_path / ".unstd-locks"
    original = {entry.name: entry.stat().st_ino for entry in directory.iterdir()}
    assert len(original) == 64
    with locking.locked(path, "name\x00space", map(str, range(1_000)), 0):
        pass
    assert original == {
        entry.name: entry.stat().st_ino for entry in directory.iterdir()
    }
    if os.name != "nt":
        assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
        assert all(
            stat.S_IMODE(entry.stat().st_mode) & 0o077 == 0
            for entry in directory.iterdir()
        )


def test_partial_acquisition_error_closes_every_owned_descriptor(tmp_path, monkeypatch):
    descriptors = []
    open_file = locking._open
    try_lock = locking._try_lock

    def observed_open(path):
        descriptor = open_file(path)
        descriptors.append(descriptor)
        return descriptor

    def fail_second(descriptor):
        if len(descriptors) == 2:
            message = "injected lock I/O failure"
            raise OSError(errno.EIO, message)
        return try_lock(descriptor)

    monkeypatch.setattr(locking, "_open", observed_open)
    monkeypatch.setattr(locking, "_try_lock", fail_second)
    with (
        pytest.raises(OSError, match="injected lock I/O failure"),
        locking.locked(tmp_path / "cache.sqlite3", "default", None, 0),
    ):
        pytest.fail("I/O failure was treated as contention")
    assert len(descriptors) == 2
    assert not locking._active_fds
    for descriptor in descriptors:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptor)
    monkeypatch.setattr(locking, "_try_lock", try_lock)
    with locking.locked(tmp_path / "cache.sqlite3", "default", None, 0):
        pass


def test_clear_shares_one_acquisition_deadline_across_all_stripes(
    tmp_path, monkeypatch
):
    clock = [0.0]
    attempted = set()
    acquisitions = []
    open_file = locking._open

    def observed_open(path):
        descriptor = open_file(path)
        acquisitions.append(descriptor)
        return descriptor

    def contended_once(descriptor):
        if descriptor not in attempted:
            attempted.add(descriptor)
            return False
        return True

    def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr(locking, "_open", observed_open)
    monkeypatch.setattr(locking, "_try_lock", contended_once)
    monkeypatch.setattr(locking, "_unlock", lambda descriptor: None)
    monkeypatch.setattr(locking.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(locking.time, "sleep", advance)
    with (
        pytest.raises(sqlite3.OperationalError, match="locked"),
        locking.locked(tmp_path / "cache.sqlite3", "default", None, 0.025),
    ):
        pytest.fail("each stripe received a fresh timeout budget")
    assert clock[0] == pytest.approx(0.025)
    assert len(acquisitions) < 64
    assert not locking._active_fds


def test_cleanup_failure_keeps_primary_error_and_still_releases_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "cache.sqlite3"
    unlock = locking._unlock

    def fail_unlock(descriptor):
        message = "injected unlock failure"
        raise OSError(errno.EIO, message)

    monkeypatch.setattr(locking, "_unlock", fail_unlock)
    with (
        pytest.raises(KeyboardInterrupt) as observed,
        locking.locked(path, "default", ["one"], 0),
    ):
        raise KeyboardInterrupt
    assert any("injected unlock failure" in note for note in observed.value.__notes__)
    assert not locking._active_fds
    monkeypatch.setattr(locking, "_unlock", unlock)
    with locking.locked(path, "default", ["one"], 0):
        pass


def test_factory_write_guard_is_canonical_and_local_to_the_producer_thread(tmp_path):
    path = tmp_path / "cache.sqlite3"
    observed = []

    def another_thread():
        locking.check_writable(path)
        observed.append("allowed")

    with locking.producing(path):
        with pytest.raises(RuntimeError, match="same database"):
            locking.check_writable(tmp_path / "child" / ".." / "cache.sqlite3")
        with (
            pytest.raises(RuntimeError, match="same database"),
            locking.locked(path, "other-namespace", ["other-key"], 0),
        ):
            pytest.fail("factory acquired a second database write lock")
        locking.check_writable(tmp_path / "other.sqlite3")
        worker = Thread(target=another_thread)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert observed == ["allowed"]
    locking.check_writable(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX fork ownership")
def test_fork_closes_child_handles_without_unlocking_parent_or_publishing(tmp_path):
    code = """
import os
from pathlib import Path
import sqlite3
from unstd.cache import locking

path = Path('cache.sqlite3')
child = None
try:
    with locking.locked(path, 'default', ['one'], 0):
        with locking.producing(path):
            child = os.fork()
            if child:
                _, status = os.waitpid(child, 0)
                assert os.waitstatus_to_exitcode(status) == 0
except RuntimeError as error:
    assert child == 0
    assert 'after fork' in str(error)
    assert not locking._active_fds
    locking.check_writable(path)
    try:
        with locking.locked(path, 'default', ['one'], 0):
            raise AssertionError('child released parent lock')
    except sqlite3.OperationalError:
        os._exit(0)
    os._exit(1)
assert child
assert not locking._active_fds
with locking.locked(path, 'default', ['one'], 0):
    pass
print('FORK_LOCK_OK')
"""
    result = run_python(code, cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "FORK_LOCK_OK\n"
