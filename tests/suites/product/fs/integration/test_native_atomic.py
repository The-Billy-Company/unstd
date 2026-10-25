"""Installed Linux dispatch must retain atomicity at real syscall boundaries.

The ELF observer is test-only. Successful syncing stays real; no production
fault flags, Python OS replacements, sleeps, or downloaded user data are used.
Each subprocess owns its paths and compares public dispatch with the preserved
Python fallback, while profiling proves which implementation actually ran.
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.process import python_argv


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native fs")
_PRIOR = b"prior committed contents"
_PAYLOAD = bytes(range(256)) * 17
_CHILD = r"""
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import signal
import stat
import sys
from unstd import fs

route, durable_json, previous, mask, kind, shape = sys.argv[1:]
native = importlib.import_module('unstd.fs._fs_native')
assert inspect.isbuiltin(native.atomic_write)
native_function = native.atomic_write
if route == 'python':
    fs._native = None
path = Path.cwd() / 'state.bin'
prior = b'prior committed contents'
payload = bytes(range(256)) * 17
options = {}
if kind == 'utf8':
    payload = 'héllo — 雪\n\x00'
elif kind == 'surrogate':
    payload = 'unencodable \ud800 text'
elif kind == 'utf16':
    payload = 'héllo — 雪\n'
    options['encoding'] = 'utf-16-le'
if shape == 'directory':
    path.mkdir()
elif shape == 'blocked-parent':
    blocker = Path.cwd() / 'blocker'
    blocker.write_bytes(prior)
    path = blocker / 'state.bin'
elif previous != 'new':
    path.write_bytes(prior)
    path.chmod(int(previous))
os.environ['FS_NATIVE_TARGET'] = str(path)

class Cancelled(Exception):
    pass
def cancel(signum, frame):
    raise Cancelled('fixture cancellation')
signal.signal(signal.SIGUSR1, cancel)
signal.signal(signal.SIGINT, signal.default_int_handler)
counts = {'native': 0, 'python': 0}
python_code = inspect.unwrap(fs._atomic_writer).__code__
def profile(frame, event, arg):
    if event == 'c_call' and arg is native_function:
        counts['native'] += 1
    if event == 'call' and frame.f_code is python_code:
        counts['python'] += 1
before = len(os.listdir('/proc/self/fd'))
os.umask(int(mask))
os.environ['FS_NATIVE_ARMED'] = '1'
sys.setprofile(profile)
result = {'ok': False}
try:
    returned = fs.atomic_write(path, payload, durable=json.loads(durable_json), **options)
    result.update(ok=True, returned_path=returned == path, returned_type=type(returned).__name__)
except BaseException as error:
    result.update(error=type(error).__name__, errno=getattr(error, 'errno', None),
                  filename=str(error.filename) if getattr(error, 'filename', None) is not None else None,
                  filename2=str(error.filename2) if getattr(error, 'filename2', None) is not None else None,
                  notes=getattr(error, '__notes__', []))
    if isinstance(error, UnicodeEncodeError):
        result.update(encoding=error.encoding, start=error.start, end=error.end, reason=error.reason)
finally:
    sys.setprofile(None)
    os.environ['FS_NATIVE_ARMED'] = '0'
    os.umask(0o022)
result['counts'] = counts
result['fd_delta'] = len(os.listdir('/proc/self/fd')) - before
result['scratch'] = sorted(p.name for p in Path.cwd().glob('.*.tmp'))
if shape == 'directory':
    result['directory_retained'] = path.is_dir()
elif shape == 'blocked-parent':
    result['blocker_sha256'] = hashlib.sha256(blocker.read_bytes()).hexdigest()
elif path.exists():
    mode = stat.S_IMODE(path.stat().st_mode)
    result['mode'] = mode
    # Inspect owner-bit-restricted files only after retaining their real mode.
    path.chmod(mode | 0o400)
    result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    result['size'] = path.stat().st_size
print(json.dumps(result))
"""


@pytest.fixture(scope="module")
def native_module():
    try:
        module = importlib.import_module("unstd.fs._fs_native")
    except ImportError:
        pytest.skip("optional Linux fs extension is unavailable")
    assert inspect.isbuiltin(module.atomic_write)
    assert module.atomic_write.__module__ == "unstd.fs._fs_native"
    return module


@pytest.fixture(scope="module")
def observer(tmp_path_factory, native_module):
    del native_module
    command = shlex.split(sysconfig.get_config_var("CC") or "cc")
    if not command or shutil.which(command[0]) is None:
        pytest.skip("a C compiler is required only for ELF boundary injection")
    output = tmp_path_factory.mktemp("fs-native-observer") / "observe.so"
    source = REPO_ROOT / "native/fs/qualification/observe.c"
    result = subprocess.run(  # noqa: S603 — configured compiler and local test source
        [
            *command,
            "-O2",
            "-std=c11",
            "-fPIC",
            "-shared",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-ldl",
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return output


def _invoke(
    root: Path,
    route: str,
    *,
    observer: Path | None = None,
    durable: bool | str = False,
    previous: int | str = 0o600,
    mask: int = 0o022,
    kind: str = "custom",
    shape: str = "file",
    **settings: str,
):
    root.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FS_NATIVE_")
    }
    log = root / "events.log"
    if observer is not None:
        log.write_text("", encoding="utf-8")
        log.chmod(0o600)
        # Sanitizer runtimes must remain first in an existing preload list.
        environment["LD_PRELOAD"] = ":".join(
            part for part in (environment.get("LD_PRELOAD", ""), str(observer)) if part
        )
        environment["FS_NATIVE_LOG"] = str(log)
    environment.update(settings)
    # The shared campaign worker deliberately replaces LD_PRELOAD in sanitizer
    # runs. This child needs its checked ASan-first + observer composition.
    # subprocess.run kills and reaps this child on its fixed timeout; it forks
    # no grandchildren and retains the normal installed/source argv selection.
    result = subprocess.run(  # noqa: S603 — selected interpreter and fixture arguments
        python_argv(
            _CHILD,
            [route, json.dumps(durable), str(previous), str(mask), kind, shape],
        ),
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    evidence["events"] = log.read_text().splitlines() if observer is not None else []
    assert evidence["fd_delta"] == 0, evidence
    return evidence


def _selected(evidence, route: str) -> None:
    if route == "native":
        assert evidence["counts"]["native"] == 1, evidence
        assert evidence["counts"]["python"] == 0, evidence
    else:
        assert evidence["counts"]["native"] == 0, evidence
        assert evidence["counts"]["python"] > 0, evidence


@pytest.mark.parametrize("route", ["native", "python"])
@pytest.mark.parametrize("durable", [False, True, "strict"])
def test_public_native_and_fallback_publish_complete_private_bytes(
    tmp_path, native_module, route, durable
):
    evidence = _invoke(tmp_path / route, route, durable=durable)
    _selected(evidence, route)
    assert evidence["ok"]
    assert evidence["returned_path"]
    assert evidence["returned_type"] == "PosixPath"
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    assert evidence["mode"] == 0o600
    assert evidence["scratch"] == []


@pytest.mark.parametrize("route", ["native", "python"])
@pytest.mark.parametrize("mask", [0o000, 0o077, 0o400, 0o777])
def test_native_creation_retains_kernel_umask_filtering(
    tmp_path, native_module, route, mask
):
    evidence = _invoke(tmp_path / route, route, previous="new", mask=mask)
    _selected(evidence, route)
    assert evidence["ok"]
    assert evidence["mode"] == 0o600 & ~mask
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()


@pytest.mark.parametrize("route", ["native", "python"])
def test_native_rewrite_preserves_explicit_sharing_under_tight_umask(
    tmp_path, native_module, route
):
    evidence = _invoke(tmp_path / route, route, previous=0o664, mask=0o077)
    _selected(evidence, route)
    assert evidence["ok"]
    assert evidence["mode"] == 0o664


@pytest.mark.parametrize("route", ["native", "python"])
def test_utf8_text_uses_public_native_dispatch_and_retains_exact_bytes(
    tmp_path, native_module, route
):
    evidence = _invoke(tmp_path / route, route, kind="utf8")
    _selected(evidence, route)
    assert evidence["ok"]
    assert evidence["sha256"] == hashlib.sha256("héllo — 雪\n\x00".encode()).hexdigest()


def test_encoding_failure_retains_python_error_fields_and_rolls_back(
    tmp_path, native_module
):
    answers = {}
    for route in ("python", "native"):
        evidence = _invoke(tmp_path / route, route, kind="surrogate")
        _selected(evidence, route)
        assert evidence["error"] == "UnicodeEncodeError"
        assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
        assert evidence["scratch"] == []
        answers[route] = evidence
    for field in ("encoding", "start", "end", "reason"):
        assert answers["python"][field] == answers["native"][field]


def test_non_utf8_codec_keeps_the_python_fallback(tmp_path, native_module):
    evidence = _invoke(tmp_path / "native", "native", kind="utf16")
    _selected(evidence, "python")
    assert evidence["ok"]
    assert (
        evidence["sha256"]
        == hashlib.sha256("héllo — 雪\n".encode("utf-16-le")).hexdigest()
    )


@pytest.mark.parametrize("number", [errno.ENOSYS, errno.EPERM], ids=["enosys", "eperm"])
def test_unavailable_native_entropy_declines_before_scratch_creation(
    tmp_path, observer, number
):
    evidence = _invoke(
        tmp_path / "native",
        "native",
        observer=observer,
        FS_NATIVE_ENTROPY_FAILURE=str(number),
    )
    assert evidence["counts"]["native"] == 1
    assert evidence["counts"]["python"] > 0
    assert evidence["ok"]
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    assert evidence["scratch"] == []
    assert evidence["events"].count("getrandom:unavailable") == 1
    assert evidence["events"].count("scratch-open") == 1
    assert evidence["events"].index("getrandom:unavailable") < evidence["events"].index(
        "scratch-open"
    )


@pytest.mark.parametrize("number", [errno.EIO, errno.EAGAIN], ids=["eio", "eagain"])
def test_native_entropy_failure_preserves_previous_file_before_scratch_creation(
    tmp_path, observer, number
):
    evidence = _invoke(
        tmp_path / "native",
        "native",
        observer=observer,
        FS_NATIVE_ENTROPY_FAILURE=str(number),
    )
    _selected(evidence, "native")
    assert not evidence["ok"]
    assert evidence["error"] == type(OSError(number, "fault")).__name__
    assert evidence["errno"] == number
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert evidence["scratch"] == []
    assert evidence["events"].count("getrandom:unavailable") == 1
    assert "scratch-open" not in evidence["events"]
    assert "rename" not in evidence["events"]


def test_interrupted_native_entropy_retries_and_publishes_complete_bytes(
    tmp_path, observer
):
    evidence = _invoke(
        tmp_path / "native",
        "native",
        observer=observer,
        FS_NATIVE_ENTROPY_FAILURE=str(errno.EINTR),
    )
    _selected(evidence, "native")
    assert evidence["ok"]
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    assert evidence["scratch"] == []
    assert evidence["events"].count("getrandom:unavailable") == 1
    assert evidence["events"].count("scratch-open") == 1
    assert evidence["events"].index("getrandom:unavailable") < evidence["events"].index(
        "scratch-open"
    )


@pytest.mark.parametrize("route", ["native", "python"])
@pytest.mark.parametrize("stage", ["write", "file-sync", "scratch-close"])
@pytest.mark.parametrize("signal_number", ["10", "2"], ids=["custom", "keyboard"])
def test_cancellation_before_rename_rolls_back_and_releases_all_owners(
    tmp_path, observer, route, stage, signal_number
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        FS_NATIVE_ACTION="signal",
        FS_NATIVE_AT=stage,
        FS_NATIVE_SIGNAL=signal_number,
    )
    _selected(evidence, route)
    assert evidence["error"] == (
        "Cancelled" if signal_number == "10" else "KeyboardInterrupt"
    )
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert evidence["scratch"] == []
    assert "signal-delivered" in evidence["events"]
    assert "rename" not in evidence["events"]


def test_native_cancellation_after_scratch_open_does_not_leak_ownership(
    tmp_path, observer
):
    evidence = _invoke(
        tmp_path / "native",
        "native",
        observer=observer,
        FS_NATIVE_ACTION="signal",
        FS_NATIVE_AT="scratch-open",
    )
    _selected(evidence, "native")
    assert evidence["error"] == "Cancelled"
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert evidence["scratch"] == []


@pytest.mark.parametrize("route", ["native", "python"])
@pytest.mark.parametrize("stage", ["rename", "directory-sync-2"])
def test_postcommit_cancellation_reports_new_bytes_and_committed_state(
    tmp_path, observer, route, stage
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        durable="strict",
        FS_NATIVE_ACTION="signal",
        FS_NATIVE_AT=stage,
    )
    _selected(evidence, route)
    assert evidence["error"] == "Cancelled"
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    assert evidence["scratch"] == []
    assert any("replacement already committed" in note for note in evidence["notes"])


@pytest.mark.parametrize("route", ["native", "python"])
def test_strict_preflight_cancellation_cannot_reach_scratch_creation(
    tmp_path, observer, route
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        durable="strict",
        FS_NATIVE_ACTION="signal",
        FS_NATIVE_AT="directory-sync-1",
    )
    _selected(evidence, route)
    assert evidence["error"] == "Cancelled"
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert evidence["scratch"] == []
    assert "scratch-open" not in evidence["events"]


@pytest.mark.parametrize(
    ("stage", "error_class", "number"),
    [
        ("scratch-open", "PermissionError", errno.EACCES),
        ("write", "OSError", errno.ENOSPC),
        ("file-sync", "OSError", errno.EIO),
        ("scratch-close", "OSError", errno.EIO),
        ("rename", "PermissionError", errno.EACCES),
    ],
)
def test_kernel_error_class_errno_and_filename_match_python_fallback(
    tmp_path, observer, stage, error_class, number
):
    answers = {}
    for route in ("native", "python"):
        evidence = _invoke(
            tmp_path / route,
            route,
            observer=observer,
            FS_NATIVE_ACTION="error",
            FS_NATIVE_AT=stage,
        )
        _selected(evidence, route)
        assert evidence["error"] == error_class
        assert evidence["errno"] == number
        assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
        assert evidence["scratch"] == []
        answers[route] = evidence
    for name in ("filename", "filename2"):
        before, after = (answers[route][name] for route in ("python", "native"))
        assert (before is None) == (after is None), answers
        if before is not None:
            assert Path(before).name.startswith(".state.bin.") == Path(
                after
            ).name.startswith(".state.bin."), answers


@pytest.mark.parametrize("route", ["native", "python"])
@pytest.mark.parametrize("stage", ["directory-sync-1", "directory-sync-2"])
def test_strict_directory_errors_preserve_the_actual_commit_boundary(
    tmp_path, observer, route, stage
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        durable="strict",
        FS_NATIVE_ACTION="error",
        FS_NATIVE_AT=stage,
    )
    _selected(evidence, route)
    assert evidence["error"] == "OSError"
    assert evidence["errno"] == errno.EIO
    committed = stage.endswith("2")
    assert (
        evidence["sha256"]
        == hashlib.sha256(_PAYLOAD if committed else _PRIOR).hexdigest()
    )
    assert any("already committed" in note for note in evidence["notes"]) == committed
    assert evidence["scratch"] == []


@pytest.mark.parametrize("route", ["native", "python"])
def test_strict_directory_cleanup_error_keeps_the_original_failure(
    tmp_path, observer, route
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        durable="strict",
        FS_NATIVE_ACTION="error",
        FS_NATIVE_AT="directory-sync-1",
        FS_NATIVE_DIRECTORY_CLEANUP_CLOSE="1",
    )
    _selected(evidence, route)
    assert evidence["error"] == "OSError"
    assert evidence["errno"] == errno.EIO
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert any("directory close failed" in note for note in evidence["notes"])
    assert "directory-cleanup-close:error" in evidence["events"]


@pytest.mark.parametrize("route", ["native", "python"])
def test_best_effort_directory_errors_keep_the_committed_success(
    tmp_path, observer, route
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        durable=True,
        FS_NATIVE_ACTION="error",
        FS_NATIVE_AT="directory-sync-1",
        FS_NATIVE_DIRECTORY_CLEANUP_CLOSE="1",
    )
    _selected(evidence, route)
    assert evidence["ok"]
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    assert "directory-sync-1:error" in evidence["events"]
    assert "directory-cleanup-close:error" in evidence["events"]


@pytest.mark.parametrize("route", ["native", "python"])
def test_short_writes_and_eintr_recover_without_losing_payload_bytes(
    tmp_path, observer, route
):
    evidence = _invoke(
        tmp_path / route, route, observer=observer, FS_NATIVE_SHORT_WRITE="1"
    )
    _selected(evidence, route)
    assert evidence["ok"]
    assert evidence["sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    assert evidence["events"].count("short-write") > 1
    assert "short-write:eintr" in evidence["events"]


@pytest.mark.parametrize("route", ["native", "python"])
def test_close_eintr_is_reported_without_retrying_a_released_descriptor(
    tmp_path, observer, route
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        FS_NATIVE_ACTION="eintr",
        FS_NATIVE_AT="scratch-close",
    )
    _selected(evidence, route)
    assert evidence["error"] == "InterruptedError"
    assert evidence["errno"] == errno.EINTR
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert evidence["events"].count("scratch-close:eintr") == 1
    assert evidence["scratch"] == []


@pytest.mark.parametrize("route", ["native", "python"])
def test_cleanup_failures_do_not_replace_the_original_kernel_error(
    tmp_path, observer, route
):
    evidence = _invoke(
        tmp_path / route,
        route,
        observer=observer,
        FS_NATIVE_ACTION="error",
        FS_NATIVE_AT="file-sync",
        FS_NATIVE_CLEANUP_CLOSE="1",
        FS_NATIVE_CLEANUP_UNLINK="1",
    )
    _selected(evidence, route)
    assert evidence["error"] == "OSError"
    assert evidence["errno"] == errno.EIO
    assert evidence["sha256"] == hashlib.sha256(_PRIOR).hexdigest()
    assert len(evidence["scratch"]) == 1
    assert any(
        "scratch descriptor cleanup failed" in note for note in evidence["notes"]
    )
    assert any("scratch file cleanup failed for" in note for note in evidence["notes"])


@pytest.mark.parametrize("shape", ["directory", "blocked-parent"])
def test_normal_filesystem_rejections_keep_python_exception_contract(
    tmp_path, native_module, shape
):
    answers = {
        route: _invoke(tmp_path / route, route, shape=shape)
        for route in ("python", "native")
    }
    assert answers["python"]["error"] == answers["native"]["error"]
    assert answers["python"]["errno"] == answers["native"]["errno"]
    assert answers["native"]["scratch"] == []
    if shape == "directory":
        assert answers["native"]["directory_retained"]
    else:
        assert answers["native"]["blocker_sha256"] == hashlib.sha256(_PRIOR).hexdigest()
