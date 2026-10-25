"""Native capture preserves exact text, pipe state, timeouts and cancellation."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import time

import pytest

from tests.support.environment.process import run_python
from unstd import proc
from unstd.proc._internal import backend


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX captured pipe kernel")


@pytest.fixture
def native():
    if backend._native is None:
        pytest.skip("optional proc native binary is absent")
    assert callable(backend._native.communicate)
    return backend._native


def test_large_unicode_and_newlines_use_native_capture(monkeypatch, native):
    observed = []
    communicate = native.communicate

    def capture(*arguments):
        result = communicate(*arguments)
        observed.append(result[2:])
        return result

    monkeypatch.setattr(native, "communicate", capture)
    # The UTF-8 snowman straddles a 64KiB boundary. Decode only after joining the
    # complete byte stream, then apply the same universal newlines as subprocess.
    out = (b"A" * 65535 + "☃".encode() + b"\r\nB\rC\n") * 16
    err = (b"E" * 65534 + "日本語".encode() + b"\rF\r\nG\n") * 16
    child = r"""
import sys
import threading
out = (b'A' * 65535 + '☃'.encode() + b'\r\nB\rC\n') * 16
err = (b'E' * 65534 + '日本語'.encode() + b'\rF\r\nG\n') * 16
left = threading.Thread(target=sys.stdout.buffer.write, args=(out,))
right = threading.Thread(target=sys.stderr.buffer.write, args=(err,))
left.start()
right.start()
left.join()
right.join()
"""
    result = proc.run([sys.executable, "-S", "-c", child], timeout=5, errors="strict")
    assert result.stdout == out.decode().replace("\r\n", "\n").replace("\r", "\n")
    assert result.stderr == err.decode().replace("\r\n", "\n").replace("\r", "\n")
    assert result.returncode == 0
    assert observed == [(False, True, True, True, True)]


@pytest.mark.parametrize("input_text", ["", "payload ☃\n"])
def test_stdin_retains_stdlib_drain_and_eof(monkeypatch, native, input_text):
    def forbidden(*arguments):
        pytest.fail("stdin-bearing command entered the output-only native kernel")

    monkeypatch.setattr(native, "communicate", forbidden)
    child = """
import sys
import threading
writer = threading.Thread(target=sys.stderr.buffer.write, args=(b'E' * 1048576,))
writer.start()
sys.stdout.buffer.write(sys.stdin.buffer.read())
writer.join()
"""
    result = proc.run(
        [sys.executable, "-S", "-c", child], timeout=5, stdin_text=input_text
    )
    assert result.stdout == input_text
    assert result.stderr == "E" * 1048576


def test_native_timeout_keeps_partial_bytes_and_reaps(tmp_path, native):
    child = r"""
import os
from pathlib import Path
import sys
import time
Path('pid').write_text(str(os.getpid()))
sys.stdout.buffer.write(b'partial\xff\n')
sys.stdout.buffer.flush()
sys.stderr.buffer.write(b'error\xfe\n')
sys.stderr.buffer.flush()
time.sleep(30)
"""
    code = f"""
import os
from pathlib import Path
import sys
from unstd import proc
from unstd.proc._internal import backend
assert backend._native is not None
communicate = backend._native.communicate
calls = []
def capture(*arguments):
    result = communicate(*arguments)
    calls.append(result[2:])
    return result
backend._native.communicate = capture
events = []
try:
    proc.run([sys.executable, '-S', '-c', {child!r}], timeout=0.5, log=events.append)
except proc.TimeoutExpired as error:
    assert error.timeout == 0.5
    assert error.stdout == b'partial\\xff\\n'
    assert error.stderr == b'error\\xfe\\n'
else:
    raise AssertionError('captured child escaped native timeout')
assert calls == [(True, True, True, False, False)], calls
assert events == []
pid = int(Path('pid').read_text())
try:
    os.waitpid(pid, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('native timeout returned an unreaped child')
try:
    os.kill(pid, 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError('native timeout returned a live child')
print('NATIVE_TIMEOUT_REAPED')
"""
    result = run_python(code, cwd=tmp_path, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "NATIVE_TIMEOUT_REAPED\n"


def test_timeout_retry_retains_captured_prefix_and_uses_stdlib(monkeypatch, native):
    calls = []
    communicate = native.communicate

    def capture(*arguments):
        result = communicate(*arguments)
        calls.append(result[2])
        return result

    monkeypatch.setattr(native, "communicate", capture)
    child = backend.Popen(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys,time; print('prefix', flush=True); "
            "time.sleep(0.3); print('suffix'); print('detail', file=sys.stderr)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        with pytest.raises(proc.TimeoutExpired) as caught:
            child.communicate(timeout=0.1)
        assert caught.value.stdout == b"prefix\n"
        assert child.communicate(timeout=3) == ("prefix\nsuffix\n", "detail\n")
        assert calls == [True]
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()
        child.stderr.close()


@pytest.mark.parametrize("nonblocking", [False, True])
def test_repeated_native_drains_preserve_descriptor_ownership_and_flags(
    native, nonblocking
):
    for _ in range(16):
        outread, outwrite = os.pipe()
        errread, errwrite = os.pipe()
        try:
            os.set_blocking(outread, not nonblocking)
            os.set_blocking(errread, not nonblocking)
            os.write(outwrite, b"out\x00\xff")
            os.write(errwrite, b"err\x00\xfe")
            os.close(outwrite)
            os.close(errwrite)
            outwrite = errwrite = -1
            result = native.communicate(outread, errread, time.monotonic() + 1)
            assert result == (
                b"out\x00\xff",
                b"err\x00\xfe",
                False,
                True,
                True,
                True,
                True,
            )
            assert os.get_blocking(outread) is (not nonblocking)
            assert os.get_blocking(errread) is (not nonblocking)
            os.fstat(outread)
            os.fstat(errread)
        finally:
            for descriptor in (outread, outwrite, errread, errwrite):
                if descriptor >= 0:
                    os.close(descriptor)


def test_native_timeout_and_setup_error_restore_pipe_flags(native):
    outread, outwrite = os.pipe()
    errread, errwrite = os.pipe()
    try:
        result = native.communicate(outread, errread, time.monotonic() + 0.002)
        assert result == (b"", b"", True, False, False, False, False)
        assert os.get_blocking(outread)
        assert os.get_blocking(errread)
        os.close(errread)
        invalid = errread
        errread = -1
        with pytest.raises(OSError, match="Bad file descriptor") as caught:
            native.communicate(outread, invalid, time.monotonic() + 1)
        assert caught.value.errno == errno.EBADF
        assert os.get_blocking(outread)
    finally:
        for descriptor in (outread, outwrite, errread, errwrite):
            if descriptor >= 0:
                os.close(descriptor)


@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
def test_sigint_inside_native_pipe_poll_reaps_and_preserves_identity(
    tmp_path, native, api
):
    child = """
import os
from pathlib import Path
import sys
import threading
Path('pid').write_text(str(os.getpid()))
print('ready', flush=True)
Path('child-ready').write_text('ready')
threading.Event().wait(30)
Path('escaped').write_text('bad')
"""
    code = f"""
import os
from pathlib import Path
import signal
import sys
import threading
import time
from unstd import proc
from unstd.proc._internal import backend
assert backend._native is not None
communicate = backend._native.communicate
calls = []
def observe(*arguments):
    calls.append(arguments)
    Path('native-ready').write_text('ready')
    return communicate(*arguments)
backend._native.communicate = observe
cancelled = KeyboardInterrupt('native capture cancellation')
def interrupt(signum, frame):
    raise cancelled
previous = signal.signal(signal.SIGINT, interrupt)
stop = threading.Event()
sender_errors = []
def send():
    deadline = time.monotonic() + 4
    while not stop.wait(0.005):
        if Path('native-ready').exists() and Path('child-ready').exists():
            os.kill(os.getpid(), signal.SIGINT)
            return
        if time.monotonic() > deadline:
            sender_errors.append('readiness deadline expired')
            return
sender = threading.Thread(target=send, daemon=True)
sender.start()
events = []
try:
    getattr(proc, sys.argv[1])([sys.executable, '-S', '-c', {child!r}],
                              timeout=6, log=events.append)
except KeyboardInterrupt as error:
    assert error is cancelled
else:
    raise AssertionError('native capture swallowed SIGINT')
finally:
    stop.set()
    sender.join(timeout=1)
    signal.signal(signal.SIGINT, previous)
    backend._native.communicate = communicate
assert not sender.is_alive()
assert not sender_errors, sender_errors
assert len(calls) == 1
assert not Path('escaped').exists()
assert events == []
pid = int(Path('pid').read_text())
try:
    os.waitpid(pid, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('native SIGINT returned an unreaped child')
try:
    os.kill(pid, 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError('native SIGINT returned a live child')
assert proc.capture([sys.executable, '-S', '-c', "print('recovered')"], timeout=3) == 'recovered'
print('NATIVE_SIGINT_REAPED')
"""
    result = run_python(code, [api], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "NATIVE_SIGINT_REAPED\n"
