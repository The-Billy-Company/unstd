"""Saved scanner evidence must be complete, successful, and wholly finding-free."""

from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.static_analysis import reports as static_analysis


_CLEAN = {
    "version": "2.1.0",
    "runs": [{"tool": {"driver": {"name": "CodeQL"}}, "results": []}],
}


def _write(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _command(path):
    return subprocess.run(  # noqa: S603 — fixed interpreter and repository module
        [sys.executable, "-m", "tests.tooling.static_analysis", "sarif", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_minimal_codeql_report_needs_neither_rules_nor_invocations(tmp_path):
    path = _write(tmp_path / "clean.sarif", _CLEAN)
    assert static_analysis.check_sarif(path) == {"files": 1, "runs": 1}
    result = _command(path)
    assert result.returncode == 0, result.stderr
    assert "1 report(s), 1 run(s)" in result.stdout


@pytest.mark.parametrize(
    "document",
    [
        "",
        " ",
        "{",
        "null",
        "[]",
        "{}",
        '{"version":"2.1.0","runs":[],"runs":[{}]}',
        '{"version":"2.1.0","runs":[],"ignored":NaN}',
        '{"version":"2.1.0","runs":[],"ignored":Infinity}',
        '{"version":"2.1.0","runs":[],"ignored":-Infinity}',
    ],
)
def test_invalid_json_or_incomplete_root_cannot_pass(tmp_path, document):
    path = tmp_path / "broken.sarif"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(static_analysis.StaticAnalysisError):
        static_analysis.check_sarif(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("version", None),
        ("version", "2.0.0"),
        ("runs", None),
        ("runs", {}),
        ("runs", []),
    ],
)
def test_version_and_nonempty_run_inventory_are_mandatory(tmp_path, key, value):
    report = deepcopy(_CLEAN)
    report[key] = value
    with pytest.raises(static_analysis.StaticAnalysisError):
        static_analysis.check_sarif(_write(tmp_path / "bad.sarif", report))


@pytest.mark.parametrize(
    "run",
    [
        None,
        [],
        {},
        {"tool": {}, "results": []},
        {"tool": {"driver": {}}, "results": []},
        {"tool": {"driver": {"name": " "}}, "results": []},
        {"tool": {"driver": {"name": 1}}, "results": []},
        {"tool": {"driver": {"name": "CodeQL"}}},
        {"tool": {"driver": {"name": "CodeQL"}}, "results": None},
        {"tool": {"driver": {"name": "CodeQL"}}, "results": {}},
    ],
)
def test_each_run_requires_named_driver_and_explicit_results_list(tmp_path, run):
    report = {"version": "2.1.0", "runs": [run]}
    with pytest.raises(static_analysis.StaticAnalysisError):
        static_analysis.check_sarif(_write(tmp_path / "bad.sarif", report))


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {"ruleId": "security-bug", "level": "error"},
        {"ruleId": "quality-bug", "level": "warning"},
        {"ruleId": "informational", "level": "note"},
        {"ruleId": "hidden", "level": "none"},
        {"ruleId": "old-bug", "baselineState": "unchanged"},
        {"ruleId": "dismissed", "suppressions": [{"kind": "inSource"}]},
        None,
    ],
)
def test_every_result_fails_regardless_of_severity_baseline_or_suppression(
    tmp_path, finding
):
    report = deepcopy(_CLEAN)
    report["runs"][0]["results"] = [finding]
    with pytest.raises(static_analysis.StaticAnalysisError, match="1 finding"):
        static_analysis.check_sarif(_write(tmp_path / "finding.sarif", report))


@pytest.mark.parametrize(
    "invocations",
    [
        None,
        {},
        [None],
        [{}],
        [{"executionSuccessful": False}],
        [{"executionSuccessful": 1}],
        [{"executionSuccessful": True, "exitCode": 2}],
        [{"executionSuccessful": True, "exitCode": False}],
        [{"executionSuccessful": True, "exitCode": "0"}],
        [{"executionSuccessful": True, "exitSignalNumber": 9}],
        [{"executionSuccessful": True, "exitSignalName": "SIGKILL"}],
        [{"executionSuccessful": True, "processStartFailureMessage": "no compiler"}],
        [{"executionSuccessful": True, "toolExecutionNotifications": None}],
        [{"executionSuccessful": True, "toolExecutionNotifications": [None]}],
        [
            {
                "executionSuccessful": True,
                "toolExecutionNotifications": [{"level": "error"}],
            }
        ],
        [
            {
                "executionSuccessful": True,
                "toolConfigurationNotifications": [{"level": "error"}],
            }
        ],
        [
            {
                "executionSuccessful": True,
                "toolExecutionNotifications": [{"level": "unknown"}],
            }
        ],
    ],
)
def test_unsuccessful_or_malformed_invocations_fail_even_with_zero_results(
    tmp_path, invocations
):
    report = deepcopy(_CLEAN)
    report["runs"][0]["invocations"] = invocations
    with pytest.raises(static_analysis.StaticAnalysisError):
        static_analysis.check_sarif(_write(tmp_path / "failure.sarif", report))


def test_successful_invocations_can_retain_warning_and_note_notifications(tmp_path):
    report = deepcopy(_CLEAN)
    report["runs"][0]["invocations"] = [
        {
            "executionSuccessful": True,
            "exitCode": 0,
            "toolExecutionNotifications": [{"level": "note"}],
            "toolConfigurationNotifications": [{"level": "warning"}],
        }
    ]
    assert static_analysis.check_sarif(_write(tmp_path / "clean.sarif", report)) == {
        "files": 1,
        "runs": 1,
    }


def test_cli_checks_all_recursively_discovered_reports_and_runs(tmp_path):
    report = deepcopy(_CLEAN)
    report["runs"].append(deepcopy(report["runs"][0]))
    _write(tmp_path / "python.sarif", report)
    nested = _write(tmp_path / "native/cpp.sarif", _CLEAN)
    result = _command(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "2 report(s), 3 run(s)" in result.stdout
    report["runs"][1]["results"] = [{"ruleId": "late-run-bug"}]
    _write(nested, report)
    result = _command(tmp_path)
    assert result.returncode == 1
    assert "late-run-bug" not in result.stdout
    assert "1 finding(s)" in result.stderr


def test_cli_refuses_empty_directory_missing_file_and_truncated_report(tmp_path):
    for path in (tmp_path, tmp_path / "missing.sarif"):
        result = _command(path)
        assert result.returncode == 1
        assert "static analysis:" in result.stderr
    path = tmp_path / "truncated.sarif"
    path.write_text('{"version": "2.1.0", "runs": [', encoding="utf-8")
    result = _command(tmp_path)
    assert result.returncode == 1
    assert "cannot read SARIF" in result.stderr
    assert "passed" not in result.stdout
