"""Real process controls prove bounded ownership and fail-closed chaos results."""

from __future__ import annotations

from copy import deepcopy
import errno
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.tooling.chaos import execution


def _run(fixture, **kwargs):
    kwargs.setdefault("wall_seconds", 8)
    kwargs.setdefault("per_test_seconds", 2)
    kwargs.setdefault("cpu_seconds", 5)
    return execution.run_group(
        "clock_entropy", backend="stdlib", _fixture=fixture, **kwargs
    )


def test_reviewed_control_preserves_exact_seed_journal_source_hash_and_artifacts(
    tmp_path,
):
    report = _run("pass", seed=2**64 - 1, artifact_dir=tmp_path)
    assert report["status"] == "passed", report
    assert report["group"] == "clock_entropy"
    assert report["backend"] == "stdlib"
    assert report["schema_version"] == report["schedule_version"] == 1
    assert report["seed"] == 2**64 - 1
    assert report["passed"] == report["collected"] == 1
    assert report["failed"] == report["skipped"] == 0
    assert report["worker_returncode"] == 0
    assert report["admission"]["schema_version"] == 1
    assert report["admission"]["seed"] == 2**64 - 1
    assert report["admission"]["backend"] == "stdlib"
    assert report["admission"]["backend_modules"] == {}
    assert report["backend_modules"] == report["backend_versions"] == {}
    assert report["enforcement"]["cpu"] is (os.name == "posix")
    assert report["enforcement"]["address_space"] is sys.platform.startswith("linux")
    assert report["environment"]["process_group_cleanup"] is (os.name == "posix")
    for relative in (
        "tests/support/chaos/journal.py",
        "tests/tooling/chaos/worker.py",
        "src/unstd/ids/uid.py",
    ):
        assert (
            report["source_sha256"][relative]
            == hashlib.sha256((execution.REPO / relative).read_bytes()).hexdigest()
        )
    assert len(report["journals"]) == 1
    nodeid, journal = next(iter(report["journals"].items()))
    assert journal == {
        "version": 1,
        "seed": 2**64 - 1,
        "scenario": nodeid,
        "events": [{"event": "reviewed.fixture", "fixture": "pass", "seed": 2**64 - 1}],
    }
    assert {case["phase"] for case in report["cases"]} == {"setup", "call", "teardown"}
    assert json.loads((tmp_path / "report.json").read_text()) == report
    assert json.loads((tmp_path / "collection.json").read_text())["nodeids"] == [nodeid]
    assert len((tmp_path / "cases.jsonl").read_text().splitlines()) == 3


def test_assertion_keeps_failure_diagnostic_and_fault_journal(tmp_path):
    report = _run("assertion", artifact_dir=tmp_path)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "assertion"
    assert report["failed"] == 1
    failed = [case for case in report["cases"] if case["outcome"] == "failed"]
    assert "reviewed campaign assertion failure" in failed[0]["diagnostic"]
    assert report["failure"]["nodeids"] == [failed[0]["nodeid"]]
    assert (
        report["journals"][failed[0]["nodeid"]]["events"][0]["fixture"] == "assertion"
    )
    assert json.loads((tmp_path / "report.json").read_text()) == report


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [
        ("skip", "incomplete_or_empty"),
        ("empty", "worker_exit"),
        ("no-journal", "missing_journal"),
    ],
)
def test_zero_evidence_or_zero_execution_never_reports_success(fixture, kind):
    report = _run(fixture)
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == kind


def test_parent_per_test_watchdog_kills_active_worker_and_keeps_identity(tmp_path):
    started = time.monotonic()
    report = _run("hang", per_test_seconds=0.2, artifact_dir=tmp_path)
    assert time.monotonic() - started < 6
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "test_timeout"
    assert "test_reviewed_campaign_control" in report["failure"]["nodeid"]
    assert report["active"]["nodeid"] == report["failure"]["nodeid"]
    assert report["admission"]["enforcement"] == report["enforcement"]
    assert (
        report["journals"][report["failure"]["nodeid"]]["events"][0]["fixture"]
        == "hang"
    )
    assert (tmp_path / "admission.json").is_file()
    assert not (tmp_path / "complete.json").exists()
    assert (tmp_path / "active.json").is_file()
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_parent_campaign_deadline_includes_startup_and_collection():
    started = time.monotonic()
    report = _run("hang", wall_seconds=0.05)
    assert time.monotonic() - started < 5
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "wall_timeout"


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal classification")
def test_real_sigabrt_is_a_native_crash_with_disabled_core_dumps(tmp_path):
    report = _run("abort", artifact_dir=tmp_path)
    assert report["status"] == "failed"
    assert report["failure"] == {"kind": "native_crash", "signal": signal.SIGABRT}
    assert report["worker_returncode"] == -signal.SIGABRT
    assert report["enforcement"]["core_dumps_disabled"] is True
    assert report["journals"]
    assert not list(tmp_path.rglob("core*"))


def test_chatter_cannot_block_worker_or_exceed_capture_budget():
    report = _run("flood")
    assert report["status"] == "passed", report
    # Worker redirects pytest's stdout to stderr, but each available pipe keeps
    # at most 16 KiB at either end and reports its actual byte count.
    captured = report["stderr"]
    assert captured["bytes"] > 200000
    assert captured["truncated"] is True
    assert len(captured["text"].encode()) <= 32768
    assert (
        "reviewed stderr head" in captured["text"]
        or "reviewed stdout head" in captured["text"]
    )
    assert (
        "reviewed stderr tail" in captured["text"]
        or "reviewed stdout tail" in captured["text"]
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group ownership")
def test_watchdog_kills_real_descendant_in_owned_process_group(tmp_path, monkeypatch):
    pidfile = tmp_path / "descendant.pid"
    monkeypatch.setenv("UNSTD_CHAOS_DESCENDANT_PID", str(pidfile))
    report = _run("descendant", per_test_seconds=0.5)
    assert report["failure"]["kind"] == "test_timeout"
    assert pidfile.is_file(), report
    pid = int(pidfile.read_text())
    result = subprocess.run(  # noqa: S603 — fixed process inspector and integer pid
        ["/bin/ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert not result.stdout.strip() or result.stdout.strip().startswith("Z"), (
        result.stdout
    )


@pytest.mark.parametrize("seed", [True, -1, 2**64, 1.0, "1", None])
def test_replay_seed_admission_happens_before_process_launch(seed, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid admission launched a worker")

    monkeypatch.setattr(execution.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        _run("pass", seed=seed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wall_seconds", 0),
        ("wall_seconds", float("inf")),
        ("wall_seconds", float("nan")),
        ("wall_seconds", True),
        ("per_test_seconds", -1),
        ("per_test_seconds", 3601),
        ("cpu_seconds", 0),
        ("cpu_seconds", 1.5),
        ("cpu_seconds", True),
        ("memory_mb", 0),
        ("memory_mb", 65537),
        ("memory_mb", False),
    ],
)
def test_resource_budget_admission_happens_before_process_launch(
    field, value, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid resource budget launched a worker")

    monkeypatch.setattr(execution.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match=field):
        _run("pass", **{field: value})


def test_unreviewed_fixture_and_unknown_groups_are_refused(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unknown target launched a worker")

    monkeypatch.setattr(execution.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="unreviewed"):
        _run("../../unreviewed")
    with pytest.raises(ValueError, match="unknown"):
        execution.run_group("../escape", backend="stdlib")


def test_reusing_artifact_destination_cannot_retain_old_completion(tmp_path):
    first = _run("pass", artifact_dir=tmp_path)
    assert first["status"] == "passed"
    assert (tmp_path / "complete.json").is_file()
    second = _run("hang", per_test_seconds=0.2, artifact_dir=tmp_path)
    assert second["status"] == "failed"
    assert not (tmp_path / "complete.json").exists()
    assert json.loads((tmp_path / "report.json").read_text()) == second


def test_forged_journal_seed_cannot_be_reported_as_executed_proof():
    report = _run("invalid-journal")
    assert report["status"] == "failed"
    assert report["failure"]["kind"] == "invalid_journal"


@pytest.mark.skipif(os.name != "posix", reason="POSIX group cleanup ownership")
def test_campaign_group_cleanup_failure_still_reaps_worker_and_finishes_capture(
    monkeypatch,
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

    monkeypatch.setattr(execution.subprocess, "Popen", record)
    monkeypatch.setattr(execution, "kill_owned_process_group", deny)
    with pytest.raises(PermissionError, match="fixture group denied") as error:
        _run("hang", wall_seconds=0.1)
    assert error.value is refused
    assert children[0].returncode == -signal.SIGKILL
    assert children[0].stdout.closed
    assert children[0].stderr.closed
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0].pid, os.WNOHANG)


@pytest.mark.skipif(os.name != "posix", reason="POSIX RLIMIT_CPU enforcement")
def test_cpu_budget_is_enforced_by_kernel_not_only_wall_watchdog():
    report = _run("cpu", cpu_seconds=1, per_test_seconds=6)
    assert report["status"] == "failed"
    assert report["failure"] == {"kind": "cpu_limit", "signal": signal.SIGXCPU}
    assert report["enforcement"]["cpu"] is True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux RLIMIT_AS enforcement"
)
def test_address_space_budget_rejects_untouched_virtual_mapping():
    report = _run("memory", memory_mb=512)
    assert report["status"] == "passed", report
    assert report["enforcement"]["address_space"] is True


def test_evidence_reader_refuses_oversized_invalid_or_partial_json(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(execution, "MAX_REPORT", 64)
    path = tmp_path / "evidence.json"
    path.write_text("x" * 65)
    with pytest.raises(ValueError, match="budget"):
        execution._read(path)
    path.write_text('{"missing":')
    with pytest.raises(json.JSONDecodeError):
        execution._read(path)
    path.write_text('{"valid":1}\n{"partial":')
    with pytest.raises(json.JSONDecodeError):
        execution._read(path, lines=True)


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate-call",
        "missing-teardown",
        "missing-setup",
        "foreign-node",
        "missing-node",
        "duplicate-collection",
        "bad-outcome",
        "missing-call",
    ],
)
def test_terminal_accounting_refuses_duplicate_missing_or_foreign_evidence(corruption):
    collection = {"count": 2, "nodeids": ["case::one", "case::two"]}
    cases = [
        {"nodeid": nodeid, "phase": phase, "outcome": "passed"}
        for nodeid in collection["nodeids"]
        for phase in ("setup", "call", "teardown")
    ]
    assert execution._valid_accounting(cases, collection, 2) is True
    if corruption == "duplicate-call":
        cases.append(deepcopy(cases[1]))
    elif corruption == "missing-teardown":
        cases.pop(2)
    elif corruption == "missing-setup":
        cases.pop(0)
    elif corruption == "foreign-node":
        cases[0]["nodeid"] = "case::foreign"
    elif corruption == "missing-node":
        cases = cases[:3]
    elif corruption == "duplicate-collection":
        collection["nodeids"][1] = "case::one"
    elif corruption == "bad-outcome":
        cases[1]["outcome"] = "rerun"
    else:
        cases.pop(1)
    assert execution._valid_accounting(cases, collection, 2) is False


def test_terminal_accounting_accepts_setup_skip_without_inventing_call():
    collection = {"count": 1, "nodeids": ["case::optional"]}
    cases = [
        {"nodeid": "case::optional", "phase": "setup", "outcome": "skipped"},
        {"nodeid": "case::optional", "phase": "teardown", "outcome": "passed"},
    ]
    assert execution._valid_accounting(cases, collection, 1) is True


@pytest.mark.parametrize(
    "corruption",
    ["seed", "version", "scenario", "event", "nan", "oversized", "extra-key"],
)
def test_journal_acceptance_requires_exact_seed_identity_and_bounded_json(corruption):
    journal = {
        "version": 1,
        "seed": 173,
        "scenario": "case::one",
        "events": [{"event": "fault"}],
    }
    assert execution._valid_journal(journal, nodeid="case::one", seed=173) is True
    if corruption == "seed":
        journal["seed"] = 174
    elif corruption == "version":
        journal["version"] = True
    elif corruption == "scenario":
        journal["scenario"] = "case::other"
    elif corruption == "event":
        journal["events"][0]["event"] = ""
    elif corruption == "nan":
        journal["events"][0]["value"] = float("nan")
    elif corruption == "oversized":
        journal["events"][0]["value"] = "x" * 65536
    else:
        journal["unexpected"] = "schema drift"
    assert execution._valid_journal(journal, nodeid="case::one", seed=173) is False


@pytest.mark.parametrize("fixture", ["hang", "abort"])
@pytest.mark.skipif(
    os.name != "posix", reason="POSIX crash and process ownership evidence"
)
def test_live_journal_keeps_planned_and_observed_faults_before_worker_dies(
    tmp_path, fixture
):
    report = _run(fixture, per_test_seconds=0.2, artifact_dir=tmp_path)
    assert report["status"] == "failed"
    assert len(report["live_journals"]) == 1
    nodeid, journal = next(iter(report["live_journals"].items()))
    assert journal["seed"] == 173
    assert [event["event"] for event in journal["events"]] == [
        "reviewed.fixture",
        "reviewed.worker.observed",
        "reviewed.fault.armed",
    ]
    assert journal["events"][-1]["fault"] == fixture
    assert journal["events"][1]["pid"] > 0
    assert report["journals"][nodeid] == journal
    path = (
        tmp_path / "journals" / (hashlib.sha256(nodeid.encode()).hexdigest() + ".json")
    )
    assert json.loads(path.read_text()) == journal
    assert not (tmp_path / "complete.json").exists()


def test_real_clock_entropy_group_produces_named_fault_observations():
    report = execution.run_group(
        "clock_entropy", backend="stdlib", seed=271, wall_seconds=20, cpu_seconds=10
    )
    assert report["status"] == "passed", report
    assert report["passed"] >= 25
    assert report["collected"] == report["passed"] + report["skipped"]
    assert len(report["journals"]) >= report["passed"]
    assert all(journal["seed"] == 271 for journal in report["journals"].values())
    assert any(
        event["event"] == "clock_entropy.observed"
        for journal in report["journals"].values()
        for event in journal["events"]
    )
