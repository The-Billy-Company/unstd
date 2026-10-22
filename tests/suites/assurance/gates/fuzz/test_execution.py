"""Real child processes prove startup, byte retention, limits and failure gates.

No crash, hang, missing backend or unexpected target exception may become a
successful replay. Mutation uses the actual optional coverage engine or fails
its startup explicitly; the deterministic worker never imports that engine.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests.tooling.fuzz import execution
from tests.tooling.fuzz.execution import run_atheris, run_inputs


def _fixture(name, cases=None, **kwargs):
    kwargs.setdefault("wall_seconds", 8)
    return run_inputs(
        "json",
        [("exact-case", b"\0arbitrary\xff\0")] if cases is None else cases,
        backend="stdlib",
        _fixture=name,
        **kwargs,
    )


def test_real_replay_runs_ordered_inputs_and_exact_completion():
    cases = [("empty", b""), ("valid", b'\0{"x":1}'), ("invalid", b"\0[bad")]
    first = run_inputs("json", cases, backend="stdlib")
    second = run_inputs("json", cases, backend="stdlib")
    assert first["status"] == second["status"] == "passed", (first, second)
    assert first["attempted"] == first["completed"] == first["cases"] == 3
    assert first["corpus_sha256"] == second["corpus_sha256"]
    assert first["worker_returncode"] == 0
    assert first["backend_modules"] == {}
    assert first["environment"]["executable"] == sys.executable


def test_order_and_content_are_both_part_of_replay_digest():
    forward = _fixture("pass", [("a", b"a"), ("b", b"b")])
    reverse = _fixture("pass", [("b", b"b"), ("a", b"a")])
    modified = _fixture("pass", [("a", b"a"), ("b", b"B")])
    assert (
        len({report["corpus_sha256"] for report in (forward, reverse, modified)}) == 3
    )


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [
        ("assertion", "assertion"),
        ("unexpected", "unexpected_exception"),
        ("memory", "memory_error"),
    ],
)
def test_findings_preserve_exact_active_bytes_and_replay_metadata(
    tmp_path, fixture, kind
):
    raw = b"\x00finding\xff\xfe\0"
    report = _fixture(fixture, [("reviewed-input", raw)], artifact_dir=tmp_path)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == kind
    assert report["attempted"] == 1
    assert report["completed"] == 0
    finding = report["failure"]
    assert Path(finding["input_path"]).read_bytes() == raw
    assert finding["input_sha256"] == hashlib.sha256(raw).hexdigest()
    saved = json.loads(Path(report["failure_report"]).read_text())
    assert saved == report
    assert saved["harness_sha256"]["worker.py"]
    assert saved["limits"]["max_input_bytes"] == 65536
    assert saved["corpus_sha256"]
    assert saved["failure"]["case_id"] == "reviewed-input"


def test_failed_second_case_never_relabels_first_as_the_reproducer(tmp_path):
    raw = b"bad second case"
    report = _fixture(
        "assertion", [("first", b"accepted"), ("second", raw)], artifact_dir=tmp_path
    )
    assert report["completed"] == 1
    assert report["attempted"] == 2
    assert report["failure"]["case_id"] == "second"
    assert Path(report["failure"]["input_path"]).read_bytes() == raw


def test_startup_failure_cannot_be_counted_as_a_zero_case_success(tmp_path):
    report = _fixture("startup", artifact_dir=tmp_path)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "startup_error"
    assert report["attempted"] == report["completed"] == 0
    assert "input_path" not in report["failure"]
    assert list(tmp_path.glob("*.bin")) == []


def test_stdlib_worker_blocks_installed_optional_backends_before_target_import():
    report = _fixture("blocked")
    assert report["status"] == "passed", report


def test_missing_required_backend_is_a_startup_finding():
    # The crypto target cannot exist under stdlib policy. This is an explicit
    # capability failure, never an import skip or an empty successful run.
    report = run_inputs("tokens", [("seed", b"\0bad")], backend="stdlib")
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "startup_error"
    assert report["attempted"] == 0


def test_installed_worker_refuses_missing_required_extra_in_site_free_python():
    worker = Path(__file__).resolve().parents[4] / "tooling/fuzz/worker.py"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-S",
            str(worker),
            "--target",
            "json",
            "--backend",
            "installed",
            "--cpu-seconds",
            "3",
            "--memory-mb",
            "1024",
            "--max-input-bytes",
            "65536",
        ],
        capture_output=True,
        timeout=5,
        check=False,
        text=True,
    )
    assert result.returncode == 2
    event = json.loads(result.stdout)
    assert event["event"] == "failure"
    assert event["kind"] == "startup_error"
    assert event["exception"] == "ModuleNotFoundError"
    assert "orjson" in event["message"]


def test_input_watchdog_kills_and_reaps_hung_worker_and_preserves_case(tmp_path):
    started = time.monotonic()
    report = _fixture("hang", artifact_dir=tmp_path, per_input_seconds=0.15)
    assert time.monotonic() - started < 5
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "input_timeout"
    assert report["worker_returncode"] != 0
    assert Path(report["failure"]["input_path"]).read_bytes() == b"\0arbitrary\xff\0"


def test_startup_and_between_case_time_are_inside_the_wall_budget():
    report = run_inputs(
        "json", [("seed", b"\0null")], backend="stdlib", wall_seconds=0.000001
    )
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "wall_timeout"
    assert report["duration_seconds"] < 5


@pytest.mark.skipif(os.name != "posix", reason="POSIX fatal signal evidence")
def test_native_abort_is_a_finding_with_exact_reproducer(tmp_path):
    report = _fixture("abort", artifact_dir=tmp_path)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "native_crash"
    assert report["failure"]["signal"] > 0
    assert Path(report["failure"]["input_path"]).read_bytes() == b"\0arbitrary\xff\0"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group ownership")
def test_timeout_reaps_descendants_before_they_can_write(tmp_path):
    marker = tmp_path / "leaked-descendant"
    report = _fixture(
        "descendant", [("child", os.fsencode(marker))], per_input_seconds=0.3
    )
    assert report["failure"]["kind"] == "input_timeout"
    assert "reviewed descendant started" in report["stderr"]
    time.sleep(1.1)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX RLIMIT_CPU")
def test_real_cpu_limit_terminates_an_uncooperative_busy_loop():
    report = _fixture("cpu", cpu_seconds=1, per_input_seconds=15, wall_seconds=20)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "cpu_limit"
    assert report["enforcement"]["cpu"] is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux RLIMIT_AS")
def test_real_address_space_limit_rejects_a_bounded_allocation():
    report = _fixture("address_space", memory_mb=1024)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "memory_error"
    assert report["enforcement"]["address_space"] is True


def test_flooded_target_output_is_drained_and_retention_is_bounded():
    report = _fixture("flood")
    assert report["status"] == "passed", report
    assert report["completed"] == 1
    assert report["stderr_bytes"] > 1_000_000
    assert len(report["stderr"].encode()) == 32768
    assert report["stderr_truncated"] is True
    assert report["stderr"].startswith("synthetic target chatter")
    assert report["stderr"].endswith("reviewed final diagnostic\n")


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [("same", b"a"), ("same", b"b")],
        [("large", b"x" * 65537)],
        [("", b"x")],
        [("wrong", "not bytes")],
    ],
)
def test_bad_corpus_admission_never_launches_a_green_worker(cases):
    with pytest.raises(ValueError, match=r"corpus|duplicate|input|case ids"):
        run_inputs("json", cases)


@pytest.mark.parametrize(
    "limits",
    [
        {"wall_seconds": 0},
        {"wall_seconds": float("nan")},
        {"per_input_seconds": float("inf")},
        {"cpu_seconds": False},
        {"memory_mb": -1},
        {"max_input_bytes": 65537},
    ],
)
def test_invalid_execution_limits_are_rejected(limits):
    with pytest.raises(ValueError, match="must be"):
        run_inputs("json", [("seed", b"x")], **limits)


@pytest.mark.parametrize(
    ("target", "backend"),
    [("../../outside", "stdlib"), ("json", "made-up-backend")],
)
def test_external_code_and_artifact_paths_are_not_target_identities(
    tmp_path, target, backend
):
    with pytest.raises(ValueError, match="reviewed fuzz registry"):
        run_inputs(target, [("seed", b"x")], backend=backend, artifact_dir=tmp_path)
    with pytest.raises(ValueError, match="reviewed fuzz registry"):
        run_atheris(target, tmp_path, backend=backend, artifact_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_worker_ignores_host_pythonpath_and_keeps_target_whitelist(tmp_path):
    shadow = tmp_path / "worker.py"
    shadow.write_text("raise RuntimeError('untrusted shadow module')")
    report = _fixture("pass")
    assert report["status"] == "passed"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            str(Path(__file__).resolve().parents[4] / "tooling/fuzz/worker.py"),
            "--help",
        ],
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--target" in result.stdout
    assert "untrusted shadow" not in result.stderr


def test_atheris_refuses_an_empty_or_oversized_seed_corpus(tmp_path):
    with pytest.raises(ValueError, match="nonempty"):
        run_atheris("json", tmp_path)
    (tmp_path / "seed").write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="exceeds"):
        run_atheris("json", tmp_path)


def test_actual_coverage_engine_runs_or_reports_missing_dependency(tmp_path):
    corpus = tmp_path / "seeds"
    corpus.mkdir()
    seed = corpus / "valid"
    seed.write_bytes(b'\0{"x":1}')
    original = seed.read_bytes()
    available = importlib.util.find_spec("atheris") is not None
    report = run_atheris(
        "json",
        corpus,
        backend="stdlib",
        runs=32,
        wall_seconds=20,
        artifact_dir=tmp_path / "findings",
    )
    if available:
        assert report["status"] == "passed", report
        assert report["engine"] == "atheris-libfuzzer"
        assert report["completed"] >= 1
        assert "-runs=32" in report["engine_flags"]
        assert "cov:" in report["stderr"]
    else:
        assert report["status"] == "failed"
        assert report["failure"]["kind"] == "startup_error"
        assert report["failure"]["exception"] == "ModuleNotFoundError"
    assert seed.read_bytes() == original
    assert list(corpus.iterdir()) == [seed]


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Darwin zombie-only process groups"
)
def test_fuzz_close_accepts_real_zombie_group_and_reaps_the_worker():
    worker = execution._Child([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 5
        while (event := worker.next(deadline))["event"] == "tick":
            pass
        assert event["event"] == "eof"
        observed = subprocess.run(  # noqa: S603 — OS-owned ps and fixture-owned group
            ["/bin/ps", "-o", "stat=", "-g", str(worker.process.pid)],
            capture_output=True,
            timeout=1,
            check=True,
        )
        assert observed.stdout.strip().startswith(b"Z")
        with pytest.raises(PermissionError) as error:
            os.killpg(worker.process.pid, signal.SIGKILL)
        assert error.value.errno == errno.EPERM
        worker.close()
        assert worker.process.returncode == 0
        assert all(not reader.is_alive() for reader in worker.readers)
    finally:
        if worker.process.poll() is None:
            worker.process.kill()
        worker.process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX group cleanup ownership")
def test_fuzz_close_reaps_worker_and_closes_pipes_when_group_cleanup_fails(monkeypatch):
    worker = execution._Child(
        [
            sys.executable,
            "-u",
            "-c",
            'import time; print(\'{"event":"ready"}\'); time.sleep(60)',
        ]
    )
    refused = PermissionError(errno.EPERM, "fixture group denied")

    def deny(_group):
        raise refused

    try:
        deadline = time.monotonic() + 5
        while (event := worker.next(deadline))["event"] == "tick":
            pass
        assert event["event"] == "ready"
        monkeypatch.setattr(execution, "kill_owned_process_group", deny)
        with pytest.raises(PermissionError, match="fixture group denied") as error:
            worker.close()
        assert error.value is refused
        assert worker.process.returncode == -signal.SIGKILL
        assert all(not reader.is_alive() for reader in worker.readers)
        assert all(
            stream.closed
            for stream in (
                worker.process.stdin,
                worker.process.stdout,
                worker.process.stderr,
            )
        )
        with pytest.raises(ChildProcessError):
            os.waitpid(worker.process.pid, os.WNOHANG)
    finally:
        if worker.process.poll() is None:
            worker.process.kill()
        worker.process.wait(timeout=5)
