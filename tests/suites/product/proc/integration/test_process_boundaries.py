"""Real child programs prove literal argv, I/O, isolation and error contracts.

Potentially blocking calls run inside a fixture-owned Python worker with a hard
parent deadline. Child facts come from stdlib argv/environment/pipe observations;
the wrapper under test supplies neither the expected facts nor the oracle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support.environment.process import isolated_environment, run_python
from unstd import proc


_HOSTILE_ARGUMENTS = [
    "",
    "two words",
    "'single' and \"double\" quotes",
    "line one\nline two\ttab",
    "; echo INJECTED > injected.txt",
    "$(touch injected.txt)",
    "`touch injected.txt`",
    "a && echo bad || echo worse | cat > injected.txt",
    "* ? [abc] ~/root $HOME %PATH% !PATH!",
    "--looks-like-an-option=; rm harmless",
    "café 日本語 ☃ 🚀 e\u0301",
    "trailing\\",
]


def test_hostile_argv_unicode_cwd_environment_and_stdin_are_exact(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "space café ; $(literal)"
    cwd.mkdir()
    stdin = "first line\nsecond ☃ line\x00tail\n"
    expected_env = {"UNSTD_ONLY": "literal $HOME ☃", "PYTHONUTF8": "1"}
    child = """
import json
import os
import sys
print(json.dumps({
    'argv': sys.argv[1:],
    'cwd': os.getcwd(),
    'env': dict(os.environ),
    'stdin': sys.stdin.read(),
}, ensure_ascii=False))
"""
    code = f"""
import json
import subprocess
import sys
from unstd import proc
payloads = json.loads(sys.argv[1])
environment = json.loads(sys.argv[2])
argv = [sys.executable, '-c', {child!r}, *payloads]
result = proc.run(argv,
                  cwd=sys.argv[3], env=environment,
                  stdin_text=json.loads(sys.argv[4]), timeout=5)
assert result.returncode == 0
assert result.stderr == ''
oracle = subprocess.run(argv, cwd=sys.argv[3], env=environment,
                        input=json.loads(sys.argv[4]), capture_output=True,
                        text=True, encoding='utf-8', errors='replace',
                        timeout=5, check=True)
assert oracle.stderr == ''
assert json.loads(result.stdout) == json.loads(oracle.stdout)
print(result.stdout, end='')
"""
    environment = isolated_environment(**expected_env)
    result = run_python(
        code,
        [
            json.dumps(_HOSTILE_ARGUMENTS),
            json.dumps(environment),
            str(cwd),
            json.dumps(stdin),
        ],
        cwd=tmp_path,
        timeout=10,
        env=isolated_environment(PYTHONUTF8="1"),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["argv"] == _HOSTILE_ARGUMENTS
    assert Path(observed["cwd"]).resolve() == cwd.resolve()
    assert observed["stdin"] == stdin
    # The stdlib oracle includes environment entries synthesized at child startup.
    actual_env = observed["env"]
    assert {name: actual_env[name] for name in environment} == environment
    assert list(cwd.iterdir()) == []
    assert list(tmp_path.iterdir()) == [cwd]


@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
def test_missing_executable_propagates_without_creating_output(
    tmp_path: Path, api: str
) -> None:
    missing = tmp_path / "executable-does-not-exist"
    with pytest.raises(FileNotFoundError) as caught:
        getattr(proc, api)([str(missing)], timeout=1)
    with pytest.raises(FileNotFoundError) as oracle:
        subprocess.run(  # noqa: S603 — fixture-owned nonexistent executable
            [str(missing)], timeout=1, check=True
        )
    assert caught.value.filename == oracle.value.filename
    assert caught.value.errno == oracle.value.errno
    assert caught.value.args == oracle.value.args
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invalid", [None, True, False, "1", object()])
def test_timeout_type_rejection_occurs_before_launch(
    monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    calls: list[object] = []

    def forbidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        pytest.fail("invalid timeout launched a subprocess")

    monkeypatch.setattr(proc, "subprocess", SimpleNamespace(run=forbidden))
    with pytest.raises(TypeError, match="positive finite"):
        proc.run(["never-launched"], timeout=invalid)
    assert calls == []


@pytest.mark.parametrize(
    "invalid",
    [
        0,
        -0.0,
        -1,
        -0.01,
        float("nan"),
        float("inf"),
        -float("inf"),
        pytest.param(10**1000, id="overflowing-integer"),
    ],
)
def test_timeout_value_rejection_occurs_before_launch(
    monkeypatch: pytest.MonkeyPatch, invalid: float
) -> None:
    calls: list[object] = []

    def forbidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        pytest.fail("invalid timeout launched a subprocess")

    monkeypatch.setattr(proc, "subprocess", SimpleNamespace(run=forbidden))
    with pytest.raises(ValueError, match="positive finite"):
        proc.run(["never-launched"], timeout=invalid)
    assert calls == []


@pytest.mark.parametrize("api", ["capture", "run_ok"])
def test_conveniences_preserve_timeout_validation(api: str) -> None:
    with pytest.raises(TypeError, match="positive finite"):
        getattr(proc, api)(["never-launched"], timeout=None)


@pytest.mark.parametrize("status", [0, 23])
def test_large_simultaneous_stdout_stderr_and_stdin_never_deadlock(
    tmp_path: Path, status: int
) -> None:
    child = """
import sys
import threading
def emit(stream, value):
    for _ in range(128):
        stream.write(value * 16384)
        stream.flush()
left = threading.Thread(target=emit, args=(sys.stdout.buffer, b'O'))
right = threading.Thread(target=emit, args=(sys.stderr.buffer, b'E'))
left.start()
right.start()
payload = sys.stdin.buffer.read()
assert payload == b'I' * (1024 * 1024 + 17)
left.join()
right.join()
sys.exit(int(sys.argv[1]))
"""
    code = f"""
import sys
from unstd import proc
result = proc.run([sys.executable, '-c', {child!r}, sys.argv[1]],
                  stdin_text='I' * (1024 * 1024 + 17), timeout=10, check=False)
assert result.returncode == int(sys.argv[1])
assert result.stdout == 'O' * (128 * 16384)
assert result.stderr == 'E' * (128 * 16384)
assert result.ok is (result.returncode == 0)
print('EXACT_BOTH_STREAMS')
"""
    result = run_python(code, [str(status)], cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "EXACT_BOTH_STREAMS\n"


@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
def test_nonzero_contract_retains_both_streams_and_checked_result(
    tmp_path: Path, api: str
) -> None:
    child = "import sys; sys.stdout.reconfigure(encoding='utf-8'); print('  output ☃  '); print('failure detail', file=sys.stderr); sys.exit(19)"
    code = f"""
import sys
from unstd import proc
argv = [sys.executable, '-c', {child!r}]
api = getattr(proc, sys.argv[1])
if sys.argv[1] == 'run_ok':
    assert api(argv, timeout=5, check=True) is False
else:
    try:
        api(argv, timeout=5)
    except proc.ProcessError as error:
        assert error.returncode == 19
        assert error.cmd == argv
        assert error.stdout == '  output ☃  \\n'
        assert error.stderr == 'failure detail\\n'
        assert error.output == error.result.stdout
        assert error.result.argv == tuple(argv)
        assert error.result.returncode == 19
        assert error.result.ok is False
        assert error.result.duration >= 0
        assert 'failure detail' in str(error)
    else:
        raise AssertionError('nonzero child accepted by checked API')
result = proc.run(argv, timeout=5, check=False)
assert result.returncode == 19
assert result.stdout == '  output ☃  \\n'
assert result.stderr == 'failure detail\\n'
assert proc.capture(argv, timeout=5, check=False) == 'output ☃'
print('CHECKED_OUTPUT')
"""
    result = run_python(code, [api], cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "CHECKED_OUTPUT\n"


@pytest.mark.parametrize("encoding", ["utf-8", "latin-1"])
def test_explicit_codec_and_error_policy_apply_to_both_streams(
    tmp_path: Path, encoding: str
) -> None:
    child = "import sys; sys.stdout.buffer.write(b'\\xff\\x00'); sys.stderr.buffer.write(b'\\xfe\\x00')"
    code = f"""
import sys
from unstd import proc
argv = [sys.executable, '-c', {child!r}]
encoding = sys.argv[1]
result = proc.run(argv, timeout=5, encoding=encoding, errors='replace')
assert result.stdout == b'\\xff\\x00'.decode(encoding, errors='replace')
assert result.stderr == b'\\xfe\\x00'.decode(encoding, errors='replace')
print('CODEC_POLICY')
"""
    result = run_python(code, [encoding], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "CODEC_POLICY\n"


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX communicate propagates strict decode errors"
)
def test_posix_strict_output_codec_error_propagates(tmp_path: Path) -> None:
    child = "import sys; sys.stdout.buffer.write(b'\\xff\\x00'); sys.stderr.buffer.write(b'\\xfe\\x00')"
    code = f"""
import sys
from unstd import proc
try:
    proc.run([sys.executable, '-c', {child!r}], timeout=5,
             encoding='utf-8', errors='strict')
except UnicodeDecodeError:
    pass
else:
    raise AssertionError('strict invalid UTF-8 accepted')
print('STRICT_DECODE_ERROR')
"""
    result = run_python(code, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "STRICT_DECODE_ERROR\n"


@pytest.mark.parametrize("status", [0, 11])
def test_log_records_exact_completed_result_before_check(
    tmp_path: Path, status: int
) -> None:
    code = """
import sys
from unstd import proc
records = []
argv = [sys.executable, '-c', 'import sys; sys.exit(int(sys.argv[1]))', sys.argv[1]]
try:
    result = proc.run(argv, timeout=5, log=records.append)
except proc.ProcessError as error:
    result = error.result
assert len(records) == 1
assert records[0] == {'event': 'proc.run', 'argv': result.argv,
                       'returncode': result.returncode, 'duration': result.duration}
assert result.returncode == int(sys.argv[1])
assert result.argv == tuple(argv)
print('ONE_COMPLETION_RECORD')
"""
    result = run_python(code, [str(status)], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ONE_COMPLETION_RECORD\n"


@pytest.mark.parametrize("api", ["run", "capture", "run_ok"])
@pytest.mark.parametrize("status", [0, 7])
def test_log_hook_failure_propagates_after_child_completed_and_reaped(
    tmp_path: Path, api: str, status: int
) -> None:
    code = """
import os
from pathlib import Path
import sys
from unstd import proc
error = RuntimeError('log hook exploded')
records = []
def log(record):
    records.append(record)
    assert Path('completed').read_text() == 'done'
    raise error
child = "import os; from pathlib import Path; import sys; print(os.getpid(), flush=True); Path('completed').write_text('done'); sys.exit(int(sys.argv[1]))"
argv = [sys.executable, '-c', child, sys.argv[2]]
try:
    getattr(proc, sys.argv[1])(argv, log=log, timeout=5)
except RuntimeError as caught:
    assert caught is error
else:
    raise AssertionError('logging failure was swallowed')
assert len(records) == 1
assert records[0]['returncode'] == int(sys.argv[2])
assert Path('completed').read_text() == 'done'
if os.name == 'posix':
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError('proc.run left an unreaped child')
print('HOOK_FAILURE_AFTER_REAP')
"""
    result = run_python(code, [api, str(status)], cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "HOOK_FAILURE_AFTER_REAP\n"
