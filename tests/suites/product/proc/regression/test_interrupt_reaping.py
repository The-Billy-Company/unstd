"""A real SIGINT must not return control while proc's direct child is unreaped.

Found by the mixed chaos schedule: subprocess.run kills an interrupted child,
then delegates wait to Popen.__exit__. communicate's KeyboardInterrupt handler
can consume that context manager's SIGINT grace period, so __exit__ returns
without reaping. The regression observes ownership through independent waitpid,
with a readiness handshake and an outer process-group watchdog.
"""

from __future__ import annotations

import os
import select
import sys
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


_NATIVE_WAIT = (sys.platform == "linux" and hasattr(os, "pidfd_open")) or (
    sys.platform == "darwin" and hasattr(select, "kqueue")
)


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGINT and waitpid ownership")
@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
@pytest.mark.parametrize(
    "closed_pipes",
    [
        pytest.param(False, id="pipe-communication"),
        pytest.param(
            True,
            id="native-exit-wait",
            marks=pytest.mark.skipif(
                not _NATIVE_WAIT, reason="pidfd or macOS kqueue required"
            ),
        ),
    ],
)
def test_sigint_propagates_after_direct_child_is_killed_and_reaped(
    tmp_path: Path, api: str, closed_pipes: bool
) -> None:
    child = f"""
import os
from pathlib import Path
import threading
Path('pid').write_text(str(os.getpid()))
Path('ready').write_text('ready')
if {closed_pipes!r}:
    os.close(1)
    os.close(2)
threading.Event().wait(30)
Path('escaped-cancellation').write_text('bad')
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

stop = threading.Event()
sender_errors = []
native_wait_calls = []
readiness_file = 'native-wait' if {closed_pipes!r} else 'ready'
native_exit_ready = backend._exit_ready
def observe_exit_wait(pid, deadline):
    native_wait_calls.append(pid)
    Path('native-wait').write_text(str(pid))
    return native_exit_ready(pid, deadline)
if {closed_pipes!r}:
    backend._exit_ready = observe_exit_wait
cancellation = KeyboardInterrupt('owned cancellation')
def interrupt_handler(signum, frame):
    raise cancellation
prior_handler = signal.signal(signal.SIGINT, interrupt_handler)
def send_interrupt():
    deadline = time.monotonic() + 5
    while not stop.wait(0.005):
        if Path(readiness_file).exists():
            os.kill(os.getpid(), signal.SIGINT)
            return
        if time.monotonic() >= deadline:
            sender_errors.append('readiness deadline expired')
            return
sender = threading.Thread(target=send_interrupt, daemon=True)
sender.start()
logs = []
interrupted = False
try:
    getattr(proc, sys.argv[1])([sys.executable, '-c', {child!r}], timeout=8, log=logs.append)
except KeyboardInterrupt as error:
    assert error is cancellation, 'cancellation exception identity was lost'
    interrupted = True
finally:
    stop.set()
    sender.join(timeout=2)
    signal.signal(signal.SIGINT, prior_handler)
assert not sender.is_alive(), 'interrupt helper leaked'
assert not sender_errors, sender_errors
assert interrupted, 'SIGINT did not propagate'
assert logs == [], 'interrupted child emitted a completion record'
assert not Path('escaped-cancellation').exists()
pid = int(Path('pid').read_text())
if {closed_pipes!r}:
    assert native_wait_calls, 'cancellation never entered the native exit wait'
    assert set(native_wait_calls) == {{pid}}, native_wait_calls
    backend._exit_ready = native_exit_ready
try:
    os.waitpid(pid, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('SIGINT returned an unreaped direct child')
try:
    os.kill(pid, 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError('SIGINT returned a live direct child')
assert proc.capture([sys.executable, '-c', "print('recovered')"], timeout=3) == 'recovered'
print('INTERRUPTED_REAPED_RECOVERED')
"""
    result = run_python(code, [api], cwd=tmp_path, timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "INTERRUPTED_REAPED_RECOVERED\n"
