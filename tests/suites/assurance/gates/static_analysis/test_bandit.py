"""Bandit's zero exit status cannot admit empty scans or ignored scanner errors."""

from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.static_analysis import reports as static_analysis


_CLEAN = {"results": [], "errors": [], "metrics": {"_totals": {"loc": 14}}}


def _write(path, report):
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _command(path):
    return subprocess.run(  # noqa: S603 — fixed interpreter and repository module
        [sys.executable, "-m", "tests.tooling.static_analysis", "bandit", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_clean_bandit_report_proves_source_was_scanned(tmp_path):
    path = _write(tmp_path / "bandit.json", _CLEAN)
    assert static_analysis.check_bandit(path) == {"lines": 14}
    result = _command(path)
    assert result.returncode == 0, result.stderr
    assert "14 source line(s) scanned" in result.stdout


@pytest.mark.parametrize("key", ["results", "errors", "metrics"])
def test_missing_evidence_is_not_a_clean_scan(tmp_path, key):
    report = deepcopy(_CLEAN)
    del report[key]
    with pytest.raises(static_analysis.StaticAnalysisError):
        static_analysis.check_bandit(_write(tmp_path / "bandit.json", report))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("results", None),
        ("results", {}),
        ("results", [{}]),
        ("errors", None),
        ("errors", {}),
        ("errors", [{"filename": "broken.py", "reason": "syntax error"}]),
        ("metrics", None),
        ("metrics", {}),
        ("metrics", {"_totals": None}),
        ("metrics", {"_totals": {}}),
    ],
)
def test_nonclean_or_incomplete_bandit_report_fails(tmp_path, key, value):
    report = deepcopy(_CLEAN)
    report[key] = value
    with pytest.raises(static_analysis.StaticAnalysisError):
        static_analysis.check_bandit(_write(tmp_path / "bandit.json", report))


@pytest.mark.parametrize("lines", [None, 0, -1, True, False, 1.5, "14"])
def test_empty_or_malformed_scan_size_cannot_pass(tmp_path, lines):
    report = deepcopy(_CLEAN)
    report["metrics"]["_totals"]["loc"] = lines
    with pytest.raises(static_analysis.StaticAnalysisError, match="source lines"):
        static_analysis.check_bandit(_write(tmp_path / "bandit.json", report))


def test_real_cli_refuses_missing_malformed_empty_and_failed_reports(tmp_path):
    path = tmp_path / "bandit.json"
    assert _command(path).returncode == 1
    for text in ("", "{", "null", '{"results":[],"results":[{}]}'):
        path.write_text(text, encoding="utf-8")
        assert _command(path).returncode == 1
    report = deepcopy(_CLEAN)
    report["errors"] = [{"filename": "invalid.py", "reason": "syntax error"}]
    result = _command(_write(path, report))
    assert result.returncode == 1
    assert "1 scanner error(s)" in result.stderr
    assert "passed" not in result.stdout


def test_all_findings_fail_including_low_severity_and_low_confidence(tmp_path):
    report = deepcopy(_CLEAN)
    report["results"] = [
        {
            "test_id": "B101",
            "issue_severity": "LOW",
            "issue_confidence": "LOW",
            "filename": "src/example.py",
        }
    ]
    result = _command(_write(tmp_path / "bandit.json", report))
    assert result.returncode == 1
    assert "1 finding(s)" in result.stderr
