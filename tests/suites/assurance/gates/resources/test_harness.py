"""Resource evidence is admitted only after a bounded, owned worker succeeds."""

from __future__ import annotations

from contextlib import suppress
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event
import time

import pytest

from tests.support import resources
from tests.support.anchors import REPO_ROOT
from tests.support.chaos import BLOCKED_BACKENDS
from tests.support.process import run_python


pytestmark = pytest.mark.skipif(
    sys.platform not in {"linux", "darwin"},
    reason="resource containment requires Linux or macOS",
)
CONTROLS = "tests.support.resource_controls"


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
        "from tests.tooling.runner import main; raise SystemExit(main())",
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
