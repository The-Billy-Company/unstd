"""Resource evidence is admitted only after a bounded, owned worker succeeds."""

from __future__ import annotations

from contextlib import suppress
import errno
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace

import pytest

from tests.support.chaos.journal import BLOCKED_BACKENDS
from tests.support.environment import process as process_support
from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.process import run_python
from tests.support.resources import runner as resources


pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "darwin"},
    reason="resource containment requires Linux or macOS",
)
CONTROLS = "tests.support.resources.controls"


def test_resource_lane_requires_explicit_selection():
    default = run_python(
        "import pytest,sys; raise SystemExit(pytest.main(sys.argv[1:]))",
        (
            "tests/suites/assurance/adversarial/resources",
            "--collect-only",
            "-q",
        ),
        cwd=REPO_ROOT,
        timeout=15,
    )
    assert default.returncode == 5, (default.stdout, default.stderr)
    assert "::test_" not in default.stdout
    explicit = run_python(
        "from tests.tooling.suites.runner import main; raise SystemExit(main())",
        ("run", "resources", "--", "--collect-only", "-q"),
        cwd=REPO_ROOT,
        timeout=15,
    )
    assert explicit.returncode == 0, (explicit.stdout, explicit.stderr)
    assert (
        "test_streaming_memory_stays_bounded_as_input_grows[installed"
        in explicit.stdout
    )
    assert (
        "test_streaming_memory_stays_bounded_as_input_grows[stdlib" in explicit.stdout
    )


@pytest.mark.parametrize("backend", ["installed", "stdlib"])
def test_success_preserves_exact_result_backend_and_measured_evidence(backend):
    report = resources.run_resource(CONTROLS, "success", backend=backend, value=73)
    assert report["result"] == {
        "value": 73,
        "bytes": 256 * 1024,
        "blocked": list(BLOCKED_BACKENDS) if backend == "stdlib" else [],
    }
    assert report["backend"] == backend
    assert report["probe"] == f"{CONTROLS}.success"
    assert Path(report["origin"]).is_relative_to(REPO_ROOT / "src/unstd")
    assert report["platform"] == sys.platform
    assert report["enforcement"] == {
        "cpu": True,
        "address_space": sys.platform == "linux",
        "rss_watchdog": sys.platform == "darwin",
    }
    assert 0 < report["cpu_seconds"] < report["wall_seconds"] < 15
    assert report["peak_rss_bytes"] > 0
    assert report["traced_peak_bytes"] >= 256 * 1024
    assert 0 <= report["traced_current_bytes"] <= report["traced_peak_bytes"]
    if backend == "stdlib":
        assert report["backend_modules"] == []


@pytest.mark.parametrize(
    ("kind", "diagnostic"),
    [
        ("assertion", "resource assertion control"),
        ("exception", "resource exception control"),
        ("abort", str(-signal.SIGABRT)),
        ("stdout", "output budget exceeded"),
        ("stderr", "output budget exceeded"),
    ],
)
def test_real_failures_cannot_be_reported_as_success(kind, diagnostic):
    with pytest.raises(AssertionError, match=diagnostic):
        resources.run_resource(CONTROLS, "failure", kind=kind)


def test_capture_retains_only_the_fixed_output_budget():
    captured, overflow = bytearray(), Event()
    resources._capture(
        io.BytesIO(b"x" * (resources.MAX_OUTPUT + 1)), captured, overflow
    )
    assert captured == b"x" * resources.MAX_OUTPUT
    assert overflow.is_set()


@pytest.mark.parametrize(
    ("kind", "diagnostic"),
    [
        ("clone", "deep retained 8 generated classes"),
        ("rex", "regex cache retained 1025 entries"),
        ("filesystem", "descriptor growth"),
    ],
)
def test_state_oracles_reject_real_cache_and_descriptor_leaks(kind, diagnostic):
    with pytest.raises(AssertionError, match=diagnostic):
        resources.run_resource(CONTROLS, "broken_state", kind=kind)


@pytest.mark.parametrize("kind", ["sleep", "gil"])
def test_parent_deadline_kills_sleep_and_a_gil_holding_native_loop(tmp_path, kind):
    marker = tmp_path / "entered"
    started = time.monotonic()
    with pytest.raises(AssertionError, match="wall deadline exceeded"):
        resources.run_resource(
            CONTROLS, "hang", timeout=2, kind=kind, marker=str(marker)
        )
    assert marker.read_text() == "entered"
    assert time.monotonic() - started < 5


def test_kernel_cpu_limit_precedes_the_parent_deadline(tmp_path):
    marker = tmp_path / "entered"
    with pytest.raises(AssertionError, match=str(-signal.SIGXCPU)):
        resources.run_resource(
            CONTROLS,
            "hang",
            timeout=8,
            cpu_seconds=1,
            kind="cpu",
            marker=str(marker),
        )
    assert marker.read_text() == "entered"


@pytest.mark.parametrize("fast", [False, True])
def test_real_memory_growth_is_rejected_before_the_parent_deadline(tmp_path, fast):
    marker = tmp_path / "entered"
    diagnostic = "MemoryError" if sys.platform == "linux" else "RSS budget exceeded"
    with pytest.raises(AssertionError, match=diagnostic):
        resources.run_resource(
            CONTROLS, "memory", timeout=8, memory_mb=128, marker=str(marker), fast=fast
        )
    assert marker.read_text() == "entered"


@pytest.mark.parametrize("finish", [True, False])
@pytest.mark.parametrize("shared_helper", [False, True])
def test_process_group_cleanup_includes_descendants_after_exit_or_timeout(
    tmp_path, finish, shared_helper
):
    marker = tmp_path / "descendant.pid"
    try:
        if finish:
            report = resources.run_resource(
                CONTROLS,
                "descendant",
                marker=str(marker),
                finish=finish,
                shared_helper=shared_helper,
            )
            assert report["result"] == {"descendant": int(marker.read_text())}
        else:
            with pytest.raises(AssertionError, match="wall deadline exceeded"):
                resources.run_resource(
                    CONTROLS,
                    "descendant",
                    timeout=2,
                    marker=str(marker),
                    finish=finish,
                    shared_helper=shared_helper,
                )
        pid = int(marker.read_text())
        state = subprocess.run(  # noqa: S603 - fixed inspector and fixture-owned PID
            ["/bin/ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        assert not state or state.startswith("Z"), state
    finally:
        if marker.exists():
            with suppress(ProcessLookupError):
                os.kill(int(marker.read_text()), signal.SIGKILL)


@pytest.mark.parametrize(
    "options",
    [
        {"module": "os"},
        {"timeout": float("nan")},
        {"value": "x" * 4096},
    ],
)
def test_invalid_probe_admission_precedes_process_launch(monkeypatch, options):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid resource request launched a worker")

    monkeypatch.setattr(resources.subprocess, "Popen", forbidden)
    arguments = {"module": CONTROLS, "function": "success", "value": 73, **options}
    with pytest.raises(ValueError, match=r"invalid resource|arguments exceed"):
        resources.run_resource(**arguments)


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, b""), (0, b"42425 42424 {uid} Z\n42426 42424 {uid} Zs+\n")],
)
def test_darwin_group_permission_race_accepts_only_verified_finished_members(
    monkeypatch, returncode, stdout
):
    refused = PermissionError(errno.EPERM, "fixture group denied")
    signals, inspections = [], []

    def kill(group, signum):
        signals.append((group, signum))
        raise refused

    def inspect(command, **options):
        inspections.append((command, options))
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout.replace(b"{uid}", str(os.getuid()).encode()),
            b"",
        )

    monkeypatch.setattr(process_support, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(process_support.os, "killpg", kill)
    monkeypatch.setattr(process_support.subprocess, "run", inspect)
    process_support.kill_owned_process_group(42424)
    assert signals == [(42424, signal.SIGKILL)]
    assert inspections == [
        (
            ["/bin/ps", "-o", "pid=,pgid=,uid=,stat=", "-g", "42424"],
            {"capture_output": True, "timeout": 1, "check": False},
        )
    ]


@pytest.mark.parametrize(
    ("kind", "returncode", "stdout", "stderr"),
    [
        ("live", 0, b"42425 42424 {uid} S\n", b""),
        ("foreign", 0, b"42425 42424 {foreign} Z\n", b""),
        ("wrong-group", 0, b"42425 42423 {uid} Z\n", b""),
        ("invalid-pid", 0, b"1 42424 {uid} Z\n", b""),
        ("malformed", 0, b"42425 42424 Z\n", b""),
        ("malformed-number", 0, b"invalid 42424 {uid} Z\n", b""),
        ("malformed-state", 0, b"42425 42424 {uid} Zombie\n", b""),
        ("nonascii", 0, b"\xff", b""),
        ("oversize", 0, b"x" * (process_support._GROUP_EVIDENCE_LIMIT + 1), b""),
        ("stderr", 0, b"42425 42424 {uid} Z\n", b"inspector warning"),
        ("failed", 2, b"", b""),
        ("inconsistent-exit", 1, b"42425 42424 {uid} Z\n", b""),
        ("missing-evidence", 0, b"", b""),
        ("timeout", 0, b"", b""),
        ("inspector-error", 0, b"", b""),
    ],
)
def test_darwin_group_permission_failures_retain_live_foreign_or_uncertain_errors(
    monkeypatch, kind, returncode, stdout, stderr
):
    refused = PermissionError(errno.EPERM, "fixture group denied")
    signals = []

    def kill(group, signum):
        signals.append((group, signum))
        raise refused

    def inspect(command, **_options):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if kind == "inspector-error":
            message = "fixture inspector unavailable"
            raise OSError(message)
        output = stdout.replace(b"{uid}", str(os.getuid()).encode()).replace(
            b"{foreign}", str(os.getuid() + 1).encode()
        )
        return subprocess.CompletedProcess(command, returncode, output, stderr)

    monkeypatch.setattr(process_support, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(process_support.os, "killpg", kill)
    monkeypatch.setattr(process_support.subprocess, "run", inspect)
    with pytest.raises(PermissionError, match="fixture group denied") as error:
        process_support.kill_owned_process_group(42424)
    assert error.value is refused
    assert signals == [(42424, signal.SIGKILL)]


@pytest.mark.parametrize(
    ("platform", "number"), [("linux", errno.EPERM), ("darwin", errno.EACCES)]
)
def test_group_permission_exemption_is_limited_to_darwin_eperm(
    monkeypatch, platform, number
):
    refused = PermissionError(number, "fixture group denied")

    def kill(_group, _signum):
        raise refused

    def forbidden(*_args, **_kwargs):
        pytest.fail("non-Darwin or non-EPERM failure queried process membership")

    monkeypatch.setattr(process_support, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(process_support.os, "killpg", kill)
    monkeypatch.setattr(process_support.subprocess, "run", forbidden)
    with pytest.raises(PermissionError) as error:
        process_support.kill_owned_process_group(42424)
    assert error.value is refused


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Darwin zombie-only group behavior"
)
def test_real_darwin_zombie_group_permission_race_is_owned_and_reaped():
    child = subprocess.Popen(
        [sys.executable, "-c", "pass"], stdout=subprocess.PIPE, start_new_session=True
    )
    try:
        assert child.stdout.read() == b""
        observed = subprocess.run(  # noqa: S603 — OS-owned ps and fixture-owned group
            ["/bin/ps", "-o", "pid=,pgid=,uid=,stat=", "-g", str(child.pid)],
            capture_output=True,
            timeout=1,
            check=True,
        )
        pid, group, owner, state = observed.stdout.split()
        assert int(pid) == int(group) == child.pid
        assert int(owner) == os.getuid()
        assert state.startswith(b"Z")
        with pytest.raises(PermissionError) as error:
            os.killpg(child.pid, signal.SIGKILL)
        assert error.value.errno == errno.EPERM
        process_support.kill_owned_process_group(child.pid)
    finally:
        assert child.wait(timeout=5) == 0
        child.stdout.close()


def test_python_worker_reaps_direct_child_when_group_cleanup_fails(
    tmp_path, monkeypatch
):
    refused = PermissionError(errno.EPERM, "fixture group denied")

    def deny(_group):
        raise refused

    monkeypatch.delenv("UNSTD_CHAOS_OWNED_GROUP", raising=False)
    monkeypatch.setattr(process_support, "kill_owned_process_group", deny)
    with (
        pytest.raises(PermissionError, match="fixture group denied") as error,
        process_support.python_worker(
            "import time; print('ready', flush=True); time.sleep(60)", cwd=tmp_path
        ) as child,
    ):
        assert process_support.worker_line(child, timeout=2) == "ready\n"
    assert error.value is refused
    assert child.returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(child.pid, os.WNOHANG)


def test_resource_worker_reaps_direct_child_when_group_cleanup_fails(
    tmp_path, monkeypatch
):
    refused = PermissionError(errno.EPERM, "fixture group denied")
    children = []
    original_popen = subprocess.Popen

    def record(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    def deny(_group):
        raise refused

    monkeypatch.setattr(resources.subprocess, "Popen", record)
    monkeypatch.setattr(resources, "kill_owned_process_group", deny)
    with pytest.raises(PermissionError, match="fixture group denied") as error:
        resources.run_resource(
            CONTROLS,
            "hang",
            timeout=0.1,
            kind="sleep",
            marker=str(tmp_path / "entered"),
        )
    assert error.value is refused
    assert children[0].returncode == -signal.SIGKILL
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0].pid, os.WNOHANG)
