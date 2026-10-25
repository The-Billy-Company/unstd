"""Native spawning preserves the public runner's process and audit contracts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python
from unstd import proc


if TYPE_CHECKING:
    from pathlib import Path


native = pytest.importorskip("unstd.proc._proc_native")
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX native spawn")


@pytest.fixture(scope="module", autouse=True)
def admitted_native_platform():
    operation = native.spawn
    accepted = []

    def observe(*args):
        result = operation(*args)
        accepted.append(result is not NotImplemented)
        return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(native, "spawn", observe)
        assert (
            proc.capture([sys.executable, "-c", "print('admitted')"], timeout=3)
            == "admitted"
        )
    if not any(accepted):
        pytest.skip("platform libc lacks safe native spawn facilities")


def test_public_run_reaches_native_spawn_and_publishes_ownership(monkeypatch):
    calls = []
    operation = native.spawn

    def observe(child, *args):
        result = operation(child, *args)
        if result is not NotImplemented:
            assert child.pid == result
            assert child._child_created is True
            calls.append(result)
        return result

    monkeypatch.setattr(native, "spawn", observe)
    result = proc.run([sys.executable, "-c", "print('native')"])
    if not calls:
        pytest.skip("platform libc lacks safe native spawn facilities")
    assert result.stdout == "native\n"
    assert result.stderr == ""
    assert len(calls) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(calls[0], os.WNOHANG)


def _executable(directory: Path, name: str = "runner") -> Path:
    directory.mkdir(parents=True)
    path = directory / name
    path.write_text(
        f"#!{getattr(sys, '_base_executable', sys.executable)}\n"
        "import json,os,sys\n"
        "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
        "'marker':os.environ.get('PROC_MARKER')},sort_keys=True))\n"
    )
    path.chmod(0o700)
    return path


@pytest.mark.parametrize("executable", ["runner", "./runner"])
def test_path_and_relative_executables_resolve_inside_child_cwd(tmp_path, executable):
    child_directory = tmp_path / "child space café"
    _executable(child_directory)
    environment = {"PATH": ".", "PROC_MARKER": "literal $HOME ☃", "PYTHONUTF8": "1"}
    result = proc.run(
        [executable, "two words", "$(literal)"], cwd=child_directory, env=environment
    )
    assert json.loads(result.stdout) == {
        "argv": ["two words", "$(literal)"],
        "cwd": str(child_directory.resolve()),
        "marker": environment["PROC_MARKER"],
    }
    assert result.stderr == ""


@pytest.mark.parametrize(
    "failure", ["missing-cwd", "file-cwd", "missing-executable", "not-executable"]
)
def test_native_failures_keep_stdlib_exception_fields(tmp_path, failure):
    cwd = tmp_path
    argv = [sys.executable, "-c", "pass"]
    if failure == "missing-cwd":
        cwd = tmp_path / "missing"
    elif failure == "file-cwd":
        cwd = tmp_path / "file"
        cwd.write_text("not a directory")
    elif failure == "missing-executable":
        argv = [str(tmp_path / "missing-program")]
    else:
        executable = tmp_path / "not-executable"
        executable.write_text("not executable")
        argv = [str(executable)]
    expected_error = {
        "missing-cwd": FileNotFoundError,
        "file-cwd": NotADirectoryError,
        "missing-executable": FileNotFoundError,
        "not-executable": PermissionError,
    }[failure]
    with pytest.raises(expected_error) as expected:
        subprocess.run(  # noqa: S603 # fixture-owned executable paths
            argv, cwd=cwd, capture_output=True, timeout=3
        )
    with pytest.raises(type(expected.value)) as actual:
        proc.run(argv, cwd=cwd, timeout=3)
    assert actual.value.errno == expected.value.errno
    assert actual.value.filename == expected.value.filename
    assert actual.value.args == expected.value.args


def test_search_only_cwd_does_not_require_directory_read_permission(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory search permissions")
    cwd = tmp_path / "search-only"
    cwd.mkdir()
    cwd.chmod(0o100)
    try:
        assert (
            proc.capture([sys.executable, "-c", "print('searchable')"], cwd=cwd)
            == "searchable"
        )
    finally:
        cwd.chmod(0o700)


def test_inheritable_parent_descriptor_does_not_reach_native_child(tmp_path):
    with (tmp_path / "private").open("wb") as stream:
        descriptor = stream.fileno()
        os.set_inheritable(descriptor, True)
        child = f"""
import errno,os
try:
    os.fstat({descriptor})
except OSError as error:
    assert error.errno == errno.EBADF
else:
    raise AssertionError('native spawn inherited parent descriptor')
print('isolated')
"""
        assert proc.capture([sys.executable, "-c", child]) == "isolated"


def test_bytes_environment_uses_stdlib_spawn_fallback(monkeypatch):
    if not os.supports_bytes_environ:
        pytest.skip("bytes environment unavailable")
    results = []
    operation = native.spawn

    def observe(*args):
        result = operation(*args)
        results.append(result)
        return result

    monkeypatch.setattr(native, "spawn", observe)
    result = proc.run(
        [sys.executable, "-c", "import os;print(os.environ['PROC_MARKER'])"],
        env={b"PROC_MARKER": b"bytes environment"},
    )
    assert result.stdout == "bytes environment\n"
    assert results == [NotImplemented]


def test_inherited_environment_includes_direct_libc_putenv(tmp_path):
    code = """
import os,sys
from unstd import proc
key = 'UNSTD_NATIVE_PUTENV_ONLY'
assert key not in os.environ
os.putenv(key, 'libc value')
try:
    child = "import os; print(os.environ['UNSTD_NATIVE_PUTENV_ONLY'])"
    assert proc.capture([sys.executable, '-c', child]) == 'libc value'
finally:
    os.unsetenv(key)
print('RAW_ENVIRONMENT')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "RAW_ENVIRONMENT\n"


def test_audit_hook_can_mutate_argv_environment_and_search_path_once(tmp_path):
    directory = tmp_path / "audited-program"
    _executable(directory)
    code = f"""
import json,sys
from unstd import proc
records = []
def audit(event, arguments):
    if event != 'subprocess.Popen':
        return
    executable, argv, cwd, environment = arguments
    records.append((executable, list(argv)))
    argv[1] = 'after audit'
    environment['PATH'] = {str(directory)!r}
    environment['PROC_MARKER'] = 'mutated environment'
sys.addaudithook(audit)
result = proc.run(['runner', 'before audit'], env={{'PATH':'/missing-before-audit'}}, timeout=3)
observed = json.loads(result.stdout)
assert observed['argv'] == ['after audit'], observed
assert observed['marker'] == 'mutated environment', observed
assert records == [('runner', ['runner', 'before audit'])], records
assert result.argv == ('runner', 'before audit')
print('AUDITED_EXACTLY_ONCE')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "AUDITED_EXACTLY_ONCE\n"


def test_audit_environment_conversion_can_mutate_original_dictionary_safely(tmp_path):
    code = """
import os,sys
from unstd import proc
records = []
class MutatingPath:
    def __init__(self, environment):
        self.environment = environment
    def __fspath__(self):
        self.environment.clear()
        for index in range(128):
            self.environment[str(index)] = str(index)
        return 'owned value'
def audit(event, arguments):
    if event == 'subprocess.Popen':
        environment = arguments[3]
        environment['PROC_MARKER'] = MutatingPath(environment)
        records.append(event)
sys.addaudithook(audit)
result = proc.run([sys.executable, '-c', "import os;print(os.environ['PROC_MARKER'])"],
                  env={'PROC_MARKER':'before'}, timeout=3)
assert result.stdout == 'owned value\\n', result
assert records == ['subprocess.Popen'], records
print('OWNED_ENVIRONMENT_ITEMS')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "OWNED_ENVIRONMENT_ITEMS\n"


def test_audit_refusal_happens_before_child_is_created(tmp_path):
    code = """
import os,sys
from pathlib import Path
from unstd import proc
refusal = RuntimeError('audit refused process')
records = []
def audit(event, arguments):
    if event == 'subprocess.Popen':
        records.append(event)
        raise refusal
sys.addaudithook(audit)
try:
    proc.run([sys.executable, '-c', "from pathlib import Path;Path('escaped').write_text('bad')"],timeout=3)
except RuntimeError as error:
    assert error is refusal
else:
    raise AssertionError('audit refusal was ignored')
assert records == ['subprocess.Popen'], records
assert not Path('escaped').exists()
try:
    os.waitpid(-1, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('audit refusal created an unreaped child')
print('REFUSED_BEFORE_SPAWN')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "REFUSED_BEFORE_SPAWN\n"


def test_native_spawn_restores_sigpipe_default():
    # The fixture explicitly invokes an OS shell; proc itself still uses shell=False.
    result = proc.run(["/bin/sh", "-c", "kill -PIPE $$; exit 23"], check=False)
    assert result.returncode == -signal.SIGPIPE


def test_cancellation_after_native_return_reaps_the_published_child(monkeypatch):
    cancellation = KeyboardInterrupt("cancel after native return")
    pids = []
    operation = native.spawn

    def interrupt(child, *args):
        result = operation(child, *args)
        if result is NotImplemented:
            return result
        assert child.pid == result
        assert child._child_created is True
        pids.append(result)
        raise cancellation

    monkeypatch.setattr(native, "spawn", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        proc.run([sys.executable, "-c", "import time;time.sleep(30)"], timeout=3)
    assert caught.value is cancellation
    assert len(pids) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_executable_path_conversion_owns_candidates_across_foreign_hooks(tmp_path):
    directory = tmp_path / "candidate-program"
    _executable(directory)
    code = f"""
import json,os
from unstd import proc
candidates = []
class MutatingDirectory:
    def __fspath__(self):
        candidates.clear()
        return '/missing-native-candidate'
candidates.extend([MutatingDirectory(), {str(directory)!r}])
os.get_exec_path = lambda environment: candidates
result = proc.run(['runner', 'owned candidates'], timeout=3)
assert json.loads(result.stdout)['argv'] == ['owned candidates'], result
assert candidates == []
print('OWNED_EXECUTABLE_CANDIDATES')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "OWNED_EXECUTABLE_CANDIDATES\n"


def test_empty_executable_search_never_publishes_process_group_pid(tmp_path):
    code = """
import os
from unstd import proc
os.get_exec_path = lambda environment: []
try:
    proc.run(['no-candidates'],timeout=3)
except ValueError as error:
    assert 'no executable candidates' in str(error), error
else:
    raise AssertionError('empty search published a fictitious child')
try:
    os.waitpid(-1, os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise AssertionError('empty executable search created a child')
print('EMPTY_SEARCH_REJECTED')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "EMPTY_SEARCH_REJECTED\n"


def test_audit_hook_can_clear_argv_without_changing_selected_executable(tmp_path):
    code = """
import shutil,subprocess,sys
from unstd import proc
executable = shutil.which('true')
assert executable is not None
records = []
def audit(event, arguments):
    if event == 'subprocess.Popen':
        records.append(event)
        arguments[1].clear()
sys.addaudithook(audit)
expected_error = None
try:
    expected = subprocess.run([executable],capture_output=True,timeout=3)
except Exception as error:
    expected_error = error
try:
    actual = proc.run([executable],timeout=3)
except Exception as error:
    assert expected_error is not None, error
    assert type(error) is type(expected_error), (error, expected_error)
    assert error.args == expected_error.args, (error, expected_error)
else:
    assert expected_error is None, expected_error
    assert actual.returncode == expected.returncode == 0
    assert actual.stdout == expected.stdout.decode() == ''
    assert actual.stderr == expected.stderr.decode() == ''
assert records == ['subprocess.Popen','subprocess.Popen'], records
print('EMPTY_AUDITED_ARGV')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "EMPTY_AUDITED_ARGV\n"


def test_foreign_converters_cannot_observe_private_partial_owners(tmp_path):
    directory = tmp_path / "gc-program"
    _executable(directory)
    code = f"""
import gc,os,sys
from unstd import proc
mode = None
prefix = b'UNSTD_NATIVE_GC_ENV_0='
expected_directory = os.fsencode({str(directory)!r})
class InspectingPath:
    def __init__(self, value):
        self.value = value
    def __fspath__(self):
        gc.collect()
        for obj in gc.get_objects():
            if mode == 'argv' and type(obj) is tuple and len(obj) == 131:
                if type(obj[0]) is bytes:
                    raise AssertionError('partial encoded argv tuple exposed to GC')
            if mode == 'environment' and type(obj) is list and obj:
                if type(obj[0]) is bytes and obj[0].startswith(prefix):
                    raise AssertionError('mutable encoded environment owner exposed to GC')
            if mode == 'paths' and type(obj) is tuple and len(obj) == 2:
                if type(obj[0]) is bytes and obj[0] == expected_directory:
                    raise AssertionError('partial executable tuple exposed to GC')
        return self.value
def audit(event, arguments):
    if event != 'subprocess.Popen':
        return
    if mode == 'argv':
        arguments[1][1] = InspectingPath('-c')
        arguments[1].extend(['tail'] * 128)
    elif mode == 'environment':
        arguments[3]['LATE_NATIVE_GC_VALUE'] = InspectingPath('owned value')
sys.addaudithook(audit)
mode = 'argv'
assert proc.capture([sys.executable, '-c', "print('argv safe')"],timeout=3) == 'argv safe'
mode = 'environment'
environment = {{f'UNSTD_NATIVE_GC_ENV_{{index}}':str(index) for index in range(128)}}
assert proc.capture([sys.executable, '-c', "import os;print(os.environ['LATE_NATIVE_GC_VALUE'])"],
                    env=environment, timeout=3) == 'owned value'
mode = 'paths'
os.get_exec_path = lambda environment: [{str(directory)!r},InspectingPath('/missing-native-gc-path')]
assert proc.run(['runner', 'gc safe'],timeout=3).returncode == 0
print('PRIVATE_OWNERS_NOT_GC_VISIBLE')
"""
    result = run_python(code, cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "PRIVATE_OWNERS_NOT_GC_VISIBLE\n"


@pytest.mark.skipif(sys.platform != "linux", reason="glibc clearenv inheritance")
def test_inherited_environment_accepts_null_libc_environ(tmp_path):
    code = """
import ctypes,os,sys
from unstd import proc
library = ctypes.CDLL(None, use_errno=True)
clear = getattr(library, 'clearenv', None)
assert clear is not None
os.putenv('UNSTD_NATIVE_CLEARENV_MARKER', 'before clearenv')
assert clear() == 0
result = proc.run([sys.executable, '-c', "import os;print(os.environ.get('UNSTD_NATIVE_CLEARENV_MARKER', 'absent'))"],timeout=3)
assert result.stdout == 'absent\\n', result
print('NULL_ENVIRON_ACCEPTED')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "NULL_ENVIRON_ACCEPTED\n"
