"""Focused native controls preserve real payloads and every bounded attempt."""

from __future__ import annotations

import json
import subprocess

import pytest

from bench import speeds
from bench.diagnostics import gzip_reads as diagnosis
from tests.support.environment.backends import require_backend


def test_original_read_workloads_and_native_control_recover_identical_bytes() -> None:
    require_backend("unstd.compress._gzip_native")
    selected, payload = diagnosis.cases()
    results = [case.run() for case in selected]
    for case, result in zip(selected, results, strict=True):
        case.check(result)
    assert results[0] == results[1] == results[2]
    assert len(results[0]) == payload["decoded_bytes"]


def test_broken_native_control_fails_before_any_timing(monkeypatch) -> None:
    native = require_backend("unstd.compress._gzip_native")
    selected, _ = diagnosis.cases()

    class BrokenInflater:
        def __init__(self, **kwargs):
            pass

        def decompress(self, *args):
            return b""

    monkeypatch.setattr(native, "Inflater", BrokenInflater)
    monkeypatch.setattr(
        speeds, "measure", lambda *a, **kw: pytest.fail("wrong work timed")
    )
    with pytest.raises(speeds.RegistryError, match="correctness audit failed"):
        speeds.timings(selected[-1:])


def _result(environment=None, payload=None):
    return {
        "environment": environment or {"python": "same runtime"},
        "payload": payload or {"decoded_sha256": "same exact bytes"},
        "cases": {"read": {"samples_ns": [10, 100, 20]}},
    }


def test_comparison_alternates_isolated_installations_once_and_keeps_all_samples(
    monkeypatch, tmp_path
) -> None:
    invocations = []

    def run(command, **kwargs):
        invocations.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(_result()), "")

    monkeypatch.setattr(diagnosis.subprocess, "run", run)
    report = diagnosis.compare(tmp_path / "before", tmp_path / "current", rounds=9)
    assert [row["revision"] for row in report["records"]] == [
        "before",
        "current",
        "current",
        "before",
    ]
    assert len(invocations) == 4
    for (command, kwargs), row in zip(invocations, report["records"], strict=True):
        installation = tmp_path / row["revision"]
        assert command[command.index("--installation") + 1] == str(installation)
        assert kwargs["env"]["PYTHONPATH"].split(diagnosis.os.pathsep) == [
            str(installation),
            str(speeds.ROOT),
        ]
        assert ("--reverse" in command) == bool(row["repetition"])
        assert row["result"]["cases"]["read"]["samples_ns"] == [10, 100, 20]


@pytest.mark.parametrize("changed", ["environment", "payload"])
def test_mismatched_comparisons_fail_and_retain_both_results(
    monkeypatch, tmp_path, changed
) -> None:
    responses = [_result(), {**_result(), changed: {"changed": True}}]
    monkeypatch.setattr(
        diagnosis.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(
            command, 0, json.dumps(responses.pop(0)), ""
        ),
    )
    output = tmp_path / "control.json"
    with pytest.raises(speeds.RegistryError, match="runtime or payload differs"):
        diagnosis.compare(tmp_path, tmp_path, rounds=9, output=output)
    assert len(json.loads(output.read_text())["records"]) == 2


def test_worker_failure_is_retained_and_never_retried(monkeypatch, tmp_path) -> None:
    invocations = []

    def run(command, **kwargs):
        invocations.append(command)
        return subprocess.CompletedProcess(command, 1, "", "native decode failed")

    monkeypatch.setattr(diagnosis.subprocess, "run", run)
    output = tmp_path / "control.json"
    with pytest.raises(subprocess.CalledProcessError):
        diagnosis.compare(tmp_path, tmp_path, rounds=9, output=output)
    assert len(invocations) == 1
    assert json.loads(output.read_text())["failure"]["stderr"] == "native decode failed"
