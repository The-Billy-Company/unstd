"""An exited fuzz leader must pin its process-group identity until cleanup."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.tooling.fuzz import execution


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX waitid with WNOWAIT retains kernel ownership",
)
_NATIVE_WAITID = getattr(os, "waitid", None)
_NATIVE_FLAGS = (
    os.WEXITED | os.WNOHANG | os.WNOWAIT if _NATIVE_WAITID is not None else 0
)


@pytest.fixture(
    autouse=True,
    params=["native", "darwin-libc"]
    if sys.platform == "darwin" and _NATIVE_WAITID is not None
    else ["platform"],
)
def exit_observer(request, monkeypatch):
    if request.param == "darwin-libc":
        monkeypatch.delattr(os, "waitid")


def _observe(pid):
    if _NATIVE_WAITID is not None:
        return _NATIVE_WAITID(os.P_PID, pid, _NATIVE_FLAGS)
    return execution._darwin_waitid_state(pid)


@pytest.mark.parametrize("driver", ["replay", "atheris-protocol"])
def test_eof_keeps_exited_leader_waitable_until_owned_group_cleanup(
    tmp_path, monkeypatch, driver
):
    original_cleanup = execution.kill_owned_process_group
    observed = []

    def inspect_before_cleanup(pgid):
        state = _observe(pgid)
        assert state is not None, "EOF cleanup began before terminal exit"
        assert state.si_pid == pgid
        assert state.si_code == getattr(os, "CLD_EXITED", 1)
        assert state.si_status == 0
        observed.append(pgid)
        original_cleanup(pgid)

    monkeypatch.setattr(execution, "kill_owned_process_group", inspect_before_cleanup)
    if driver == "replay":
        report = execution.run_inputs("json", [("seed", b"\x00null")], backend="stdlib")
    else:
        # Exercise the real Atheris supervisor's EOF path with a reviewed framed
        # protocol worker. The actual coverage engine has its separate real gate.
        frames = [
            {
                "event": "ready",
                "enforcement": {},
                "backend_modules": {},
                "engine_version": "ownership-fixture",
                "engine_flags": [],
            },
            {
                "event": "start",
                "id": "seed",
                "data": base64.b64encode(b"\x00null").decode(),
            },
            {"event": "done", "id": "seed"},
        ]
        code = f"import sys; sys.stdout.write({''.join(json.dumps(frame) + chr(10) for frame in frames)!r})"
        monkeypatch.setattr(
            execution, "_command", lambda *args, **kwargs: [sys.executable, "-c", code]
        )
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "seed").write_bytes(b"\x00null")
        report = execution.run_atheris("json", corpus, backend="stdlib", runs=1)
    assert report["status"] == "passed", report
    assert report["worker_returncode"] == 0
    assert len(observed) == 1
    with pytest.raises(ChildProcessError):
        _observe(observed[0])


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("pass", 0),
        ("raise SystemExit(23)", 23),
        ("import os,signal; os.kill(os.getpid(), signal.SIGTERM)", -signal.SIGTERM),
    ],
)
def test_wait_exit_observes_status_without_reaping_or_setting_popen_returncode(
    code, expected
):
    worker = execution._Child([sys.executable, "-c", code])
    try:
        assert worker.wait_exit(timeout=5) == expected
        assert worker.process.returncode is None
        first = _observe(worker.process.pid)
        assert first is not None
        assert first.si_pid == worker.process.pid
        assert worker.wait_exit(timeout=0) == expected
        second = _observe(worker.process.pid)
        assert second is not None
        assert (second.si_pid, second.si_code, second.si_status) == (
            first.si_pid,
            first.si_code,
            first.si_status,
        )
    finally:
        worker.close()
    assert worker.process.returncode == expected
    assert all(not reader.is_alive() for reader in worker.readers)
    with pytest.raises(ChildProcessError):
        _observe(worker.process.pid)


def test_wait_exit_deadline_leaves_running_worker_owned_for_group_cleanup():
    worker = execution._Child([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            worker.wait_exit(timeout=0.02)
        assert time.monotonic() - started < 2
        assert worker.process.returncode is None
        assert _observe(worker.process.pid) is None
    finally:
        worker.close()
    assert worker.process.returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        _observe(worker.process.pid)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin64 siginfo_t ABI")
def test_darwin_adapter_matches_the_sdk_siginfo_layout():
    import ctypes

    _operation, info_type = execution._darwin_waitid_adapter()
    assert ctypes.sizeof(info_type) == 104
    assert ctypes.alignment(info_type) == 8
    assert info_type.si_pid.offset == 12
    assert info_type.si_uid.offset == 16
    assert info_type.si_status.offset == 20
    with pytest.raises(ChildProcessError):
        execution._darwin_waitid_state(os.getpid())


@pytest.mark.parametrize("scenario", ["replay", "startup", "missing-backend"])
def test_real_startup_and_replay_loops_keep_group_ownership_under_load(
    monkeypatch, scenario
):
    original_cleanup = execution.kill_owned_process_group
    observed = []

    def inspect_before_cleanup(pgid):
        # A running child returns None; an exited child remains waitable.
        # Either state is owned. A prematurely reaped child raises ECHILD.
        _observe(pgid)
        observed.append(pgid)
        original_cleanup(pgid)

    monkeypatch.setattr(execution, "kill_owned_process_group", inspect_before_cleanup)

    def run(_index):
        target = "tokens" if scenario == "missing-backend" else "json"
        options = {"_fixture": "startup"} if scenario == "startup" else {}
        report = execution.run_inputs(
            target, [("seed", b"\x00null")], backend="stdlib", **options
        )
        if scenario == "replay":
            assert report["status"] == "passed", report
            assert report["completed"] == 1
        else:
            assert report["status"] == "failed", report
            assert report["failure"]["kind"] == "startup_error"
            assert report["attempted"] == report["completed"] == 0
        return report["status"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(run, range(6)))
    assert len(outcomes) == len(observed) == 6
