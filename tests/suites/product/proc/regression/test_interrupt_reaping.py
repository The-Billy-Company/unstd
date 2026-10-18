"""A real SIGINT must not return control while proc's direct child is unreaped.

Found by the mixed chaos schedule: subprocess.run kills an interrupted child,
then delegates wait to Popen.__exit__. communicate's KeyboardInterrupt handler
can consume that context manager's SIGINT grace period, so __exit__ returns
without reaping. The regression observes ownership through independent waitpid,
with a readiness handshake and an outer process-group watchdog.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.support.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGINT and waitpid ownership")
@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
def test_sigint_propagates_after_direct_child_is_killed_and_reaped(
    tmp_path: Path, api: str
) -> None:
    child = """
import os
from pathlib import Path
import threading
Path('pid').write_text(str(os.getpid()))
Path('ready').write_text('ready')
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

stop = threading.Event()
sender_errors = []
cancellation = KeyboardInterrupt('owned cancellation')
def interrupt_handler(signum, frame):
    raise cancellation
prior_handler = signal.signal(signal.SIGINT, interrupt_handler)
def send_interrupt():
    deadline = time.monotonic() + 5
    while not stop.wait(0.005):
        if Path('ready').exists():
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
