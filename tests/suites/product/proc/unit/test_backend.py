"""Native exit notifications preserve stdlib ownership, deadlines and isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import os
import select
import subprocess
import sys
import time

import pytest

from unstd.proc._internal import backend


_NATIVE = (sys.platform == "linux" and hasattr(os, "pidfd_open")) or (
    sys.platform == "darwin" and hasattr(select, "kqueue")
)
_CLOSE_THEN_SLEEP = "import os,time; os.close(1); os.close(2); time.sleep(0.15)"


def _launch(code: str) -> backend.Popen:
    return backend.Popen(  # fixture-owned literal Python code
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _dispose(child: backend.Popen) -> None:
    if child.poll() is None:
        child.kill()
    child.wait()
    if child.stdout is not None:
        child.stdout.close()
    if child.stderr is not None:
        child.stderr.close()


@pytest.mark.skipif(not _NATIVE, reason="pidfd or macOS kqueue required")
def test_early_closed_pipes_use_exit_readiness_and_preserve_status(monkeypatch):
    calls = []
    native = backend._exit_ready

    def observe(pid, deadline):
        result = native(pid, deadline)
        calls.append((pid, result))
        return result

    monkeypatch.setattr(backend, "_exit_ready", observe)
    child = _launch(_CLOSE_THEN_SLEEP + "; raise SystemExit(17)")
    try:
        assert child.communicate(timeout=3) == ("", "")
        assert child.returncode == 17
        assert calls == [(child.pid, True)]
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        _dispose(child)


@pytest.mark.skipif(os.name != "posix", reason="POSIX waitpid ownership")
def test_early_closed_pipes_do_not_turn_bounded_wait_into_blocking_wait():
    child = _launch("import os,time; os.close(1); os.close(2); time.sleep(30)")
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            child.communicate(timeout=0.1)
        assert child.poll() is None
    finally:
        _dispose(child)
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


@pytest.mark.parametrize("failure", [None, errno.ENOSYS, errno.EMFILE, errno.ESRCH])
def test_missing_or_failed_native_setup_preserves_remaining_deadline(
    monkeypatch, failure
):
    time_reads = iter([10.0, 10.04])
    monkeypatch.setattr(backend.time, "monotonic", lambda: next(time_reads))
    waits = []

    def standard_wait(self, timeout):
        waits.append(timeout)
        return 19

    def unavailable(pid, deadline):
        assert pid == 12345
        assert deadline == 10.1
        if failure is not None:
            raise OSError(failure, "fixture native setup failure")

    monkeypatch.setattr(subprocess.Popen, "_wait", standard_wait)
    monkeypatch.setattr(backend, "_exit_ready", unavailable)
    # No OS child is created by this deterministic deadline test.
    receiver = backend.Popen.__new__(backend.Popen)
    receiver.pid, receiver.args, receiver.poll = 12345, ("fixture",), lambda: None
    assert receiver._wait(0.1) == 19
    assert waits == [pytest.approx(0.06)]


@pytest.mark.parametrize("error", [KeyboardInterrupt("cancelled"), RuntimeError("bug")])
def test_native_cancellation_and_unexpected_failures_are_not_fallbacks(
    monkeypatch, error
):
    def fail(pid, deadline):
        raise error

    monkeypatch.setattr(backend, "_exit_ready", fail)
    receiver = backend.Popen.__new__(backend.Popen)
    receiver.pid, receiver.args, receiver.poll = 12345, ("fixture",), lambda: None
    with pytest.raises(type(error)) as caught:
        receiver._wait(1)
    assert caught.value is error


def test_already_reaped_child_does_not_allocate_notification(monkeypatch):
    def forbidden(pid, deadline):
        pytest.fail("already reaped child allocated a notification")

    monkeypatch.setattr(backend, "_exit_ready", forbidden)
    receiver = backend.Popen.__new__(backend.Popen)
    receiver.poll = lambda: 23
    assert receiver._wait(1) == 23


@pytest.mark.skipif(not _NATIVE, reason="pidfd or macOS kqueue required")
@pytest.mark.parametrize("outcome", ["ready", "timeout", "cancelled", "setup-error"])
def test_notification_descriptor_is_not_inheritable_and_is_closed(monkeypatch, outcome):
    descriptors = []
    cancellation = KeyboardInterrupt("cancel readiness wait")

    def maybe_fail():
        if outcome == "cancelled":
            raise cancellation
        if outcome == "setup-error":
            raise OSError(errno.ENOSYS, "fixture native registration failure")

    if sys.platform == "linux":
        real_open = os.pidfd_open
        real_poll = select.poll

        def capture_open(pid, flags):
            fd = real_open(pid, flags)
            descriptors.append(fd)
            assert not os.get_inheritable(fd)
            return fd

        class Poll:
            def __init__(self):
                self.poller = real_poll()

            def register(self, fd, eventmask):
                if outcome == "setup-error":
                    maybe_fail()
                self.poller.register(fd, eventmask)

            def poll(self, timeout):
                maybe_fail()
                return self.poller.poll(timeout)

        monkeypatch.setattr(os, "pidfd_open", capture_open)
        monkeypatch.setattr(select, "poll", Poll)
    else:
        real_queue = select.kqueue

        class Queue:
            def __init__(self):
                self.queue = real_queue()
                descriptors.append(self.queue.fileno())
                assert not os.get_inheritable(self.queue.fileno())

            def control(self, changes, maxevents, timeout):
                maybe_fail()
                return self.queue.control(changes, maxevents, timeout)

            def close(self):
                self.queue.close()

        monkeypatch.setattr(select, "kqueue", Queue)

    child = _launch(_CLOSE_THEN_SLEEP)
    try:
        if outcome in {"cancelled", "setup-error"}:
            error = KeyboardInterrupt if outcome == "cancelled" else OSError
            with pytest.raises(error) as caught:
                backend._exit_ready(child.pid, time.monotonic() + 3)
            if outcome == "cancelled":
                assert caught.value is cancellation
        else:
            timeout = 3 if outcome == "ready" else 0.001
            assert backend._exit_ready(child.pid, time.monotonic() + timeout) is (
                outcome == "ready"
            )
        assert len(descriptors) == 1
        with pytest.raises(OSError, match="Bad file descriptor") as caught:
            os.fstat(descriptors[0])
        assert caught.value.errno == errno.EBADF
    finally:
        _dispose(child)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor isolation")
def test_default_close_fds_still_excludes_inheritable_parent_descriptor(tmp_path):
    with (tmp_path / "private").open("w") as private:
        descriptor = private.fileno()
        os.set_inheritable(descriptor, True)
        code = f"""
import errno,os
try:
    os.fstat({descriptor})
except OSError as error:
    assert error.errno == errno.EBADF
else:
    raise AssertionError('inherited private descriptor')
"""
        child = _launch(code)
        try:
            assert child.communicate(timeout=3) == ("", "")
            assert child.returncode == 0
        finally:
            _dispose(child)


def test_concurrent_children_keep_their_own_status_and_notifications():
    def execute(status):
        child = _launch(_CLOSE_THEN_SLEEP + f"; raise SystemExit({status})")
        try:
            assert child.communicate(timeout=3) == ("", "")
            return child.returncode
        finally:
            _dispose(child)

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert list(workers.map(execute, range(8))) == list(range(8))
