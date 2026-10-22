"""Parent-bounded real timeout, partial-output, signal, and reaping contracts."""

from __future__ import annotations

import os
import signal
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
def test_timeout_keeps_partial_output_kills_and_reaps_direct_child(
    tmp_path: Path, api: str
) -> None:
    child = """
import os
from pathlib import Path
import sys
import threading
Path('child-pid').write_text(str(os.getpid()))
sys.stdout.buffer.write(b'partial stdout\\x00\\xff\\n')
sys.stdout.buffer.flush()
sys.stderr.buffer.write(b'partial stderr\\x00\\xfe\\n')
sys.stderr.buffer.flush()
threading.Event().wait(30)
Path('should-never-complete').write_text('escaped timeout')
"""
    code = f"""
import os
from pathlib import Path
import subprocess
import sys
from unstd import proc
argv = [sys.executable, '-c', {child!r}]
events = []
api = sys.argv[1]
if api == 'run_ok':
    assert proc.run_ok(argv, timeout=1, log=events.append) is False
else:
    try:
        subprocess.run(argv, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=1)
    except subprocess.TimeoutExpired as oracle:
        expected_stdout, expected_stderr = oracle.stdout, oracle.stderr
        assert expected_stdout in (
            b'partial stdout\\x00\\xff\\n', 'partial stdout\\x00�\\n')
        assert expected_stderr in (
            b'partial stderr\\x00\\xfe\\n', 'partial stderr\\x00�\\n')
    else:
        raise AssertionError('stdlib blocking child escaped timeout')
    try:
        getattr(proc, api)(argv, timeout=1, log=events.append)
    except proc.TimeoutExpired as error:
        assert error.timeout == 1
        assert tuple(error.cmd) == tuple(argv)
        assert error.stdout == expected_stdout
        assert error.output == error.stdout
        assert error.stderr == expected_stderr
    else:
        raise AssertionError('blocking child escaped timeout')
assert events == [], 'an incomplete run must not emit a completion record'
pid = int(Path('child-pid').read_text())
assert not Path('should-never-complete').exists()
if os.name == 'posix':
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError('direct child was not reaped')
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('timed-out direct child is still running')
assert proc.capture([sys.executable, '-c', "print('recovered')"], timeout=5) == 'recovered'
print('TIMED_OUT_AND_REAPED')
"""
    result = run_python(code, [api], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "TIMED_OUT_AND_REAPED\n"
    assert not (tmp_path / "should-never-complete").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal exit status contract")
@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGKILL"])
def test_signal_exit_is_negative_and_checked_error_retains_output(
    tmp_path: Path, signal_name: str
) -> None:
    termination_signal = getattr(signal, signal_name)
    child = """
import os
import signal
import sys
print('before signal', flush=True)
print('signal detail', file=sys.stderr, flush=True)
os.kill(os.getpid(), int(sys.argv[1]))
"""
    code = f"""
import os
import sys
from unstd import proc
signum = int(sys.argv[1])
argv = [sys.executable, '-c', {child!r}, str(signum)]
result = proc.run(argv, timeout=5, check=False)
assert result.returncode == -signum
assert result.ok is False
assert result.stdout == 'before signal\\n'
assert result.stderr == 'signal detail\\n'
try:
    proc.run(argv, timeout=5)
except proc.ProcessError as error:
    assert error.returncode == -signum
    assert error.result.stdout == result.stdout
    assert error.result.stderr == result.stderr
    assert 'signal detail' in str(error)
else:
    raise AssertionError('signaled child was accepted by checked run')
assert proc.run_ok(argv, timeout=5) is False
try:
    os.waitpid(-1, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('signaled child was not reaped')
print('SIGNAL_STATUS_AND_REAP')
"""
    result = run_python(code, [str(int(termination_signal))], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "SIGNAL_STATUS_AND_REAP\n"


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX communicate propagates strict encode errors"
)
def test_posix_invalid_stdin_encoding_still_reaps_child(tmp_path: Path) -> None:
    code = """
import os
import sys
from unstd import proc
argv = [sys.executable, '-c', 'import sys; sys.stdin.buffer.read()']
try:
    proc.run(argv, stdin_text='cannot encode ☃', encoding='ascii', errors='strict', timeout=2)
except UnicodeEncodeError:
    pass
else:
    raise AssertionError('unencodable stdin was silently accepted')
if os.name == 'posix':
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError('encoding failure left an unreaped child')
print('ENCODING_FAILURE_REAPED')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ENCODING_FAILURE_REAPED\n"


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX bounded pipe drain and group cleanup"
)
def test_timeout_remains_bounded_when_descendant_retains_captured_pipes(
    tmp_path: Path,
) -> None:
    child = """
from pathlib import Path
import subprocess
import sys
descendant = subprocess.Popen([
    sys.executable, '-c', 'import threading; threading.Event().wait(30)'
])
Path('descendant-pid').write_text(str(descendant.pid))
print('DIRECT_CHILD_EXITED', flush=True)
"""
    code = f"""
import os
from pathlib import Path
import sys
from unstd import proc
argv = [sys.executable, '-c', {child!r}]
try:
    proc.run(argv, timeout=0.5)
except proc.TimeoutExpired as error:
    assert error.timeout == 0.5
    assert error.stdout == b'DIRECT_CHILD_EXITED\\n'
else:
    raise AssertionError('descendant pipe retention did not trigger the timeout')
descendant_pid = int(Path('descendant-pid').read_text())
# The product promises direct-child termination, not descendant-tree cleanup.
# This descendant intentionally remains alive with inherited stdout/stderr.
os.kill(descendant_pid, 0)
try:
    os.waitpid(-1, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('direct child was not reaped')
print('BOUNDED_WITH_OPEN_DESCENDANT_PIPES')
"""
    # The support worker owns a new POSIX process group and kills that entire
    # fixture-owned group on exit, including the deliberately retained descendant.
    result = run_python(code, cwd=tmp_path, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "BOUNDED_WITH_OPEN_DESCENDANT_PIPES\n"
