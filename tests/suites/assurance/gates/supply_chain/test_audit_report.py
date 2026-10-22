"""Advisory evidence cannot hide findings behind a successful scanner exit."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.tooling.supply_chain import policy as supply_chain


if TYPE_CHECKING:
    from pathlib import Path


def _report(path: Path, **changes: object) -> Path:
    data = {
        "schema": {"version": "preview"},
        "summary": {
            "audited_packages": 55,
            "vulnerabilities": 0,
            "adverse_statuses": 0,
        },
        "vulnerabilities": [],
        "adverse_statuses": [],
    }
    data.update(changes)
    path.write_text(json.dumps(data))
    return path


def test_clean_report_has_a_positive_audited_count_and_no_findings(tmp_path, capsys):
    path = _report(tmp_path / "audit.json")
    assert supply_chain.validate_audit(path) == {
        "audited_packages": 55,
        "vulnerabilities": 0,
        "adverse_statuses": 0,
    }
    assert supply_chain.main(["audit-report", str(path)]) == 0
    assert (
        "55 packages, no vulnerabilities or adverse statuses" in capsys.readouterr().out
    )


@pytest.mark.parametrize("field", ["vulnerabilities", "adverse_statuses"])
def test_findings_are_rejected_even_when_the_scanner_would_exit_successfully(
    tmp_path, field
):
    summary = {"audited_packages": 55, "vulnerabilities": 0, "adverse_statuses": 0}
    summary[field] = 1
    path = _report(
        tmp_path / "audit.json",
        summary=summary,
        **{field: [{"id": "security-finding"}]},
    )
    with pytest.raises(supply_chain.SupplyChainError, match="require remediation"):
        supply_chain.validate_audit(path)


@pytest.mark.parametrize("field", ["vulnerabilities", "adverse_statuses"])
def test_a_clean_summary_cannot_launder_findings_or_missing_findings(tmp_path, field):
    path = _report(tmp_path / "audit.json", **{field: [{"id": "security-finding"}]})
    with pytest.raises(supply_chain.SupplyChainError, match="count differs"):
        supply_chain.validate_audit(path)
    summary = {"audited_packages": 55, "vulnerabilities": 0, "adverse_statuses": 0}
    summary[field] = 1
    _report(path, summary=summary)
    with pytest.raises(supply_chain.SupplyChainError, match="count differs"):
        supply_chain.validate_audit(path)


@pytest.mark.parametrize(
    "field", ["audited_packages", "vulnerabilities", "adverse_statuses"]
)
@pytest.mark.parametrize("value", [-1, True, 0.5, "0", None])
def test_counts_are_nonnegative_integers_not_coercible_values(tmp_path, field, value):
    summary = {"audited_packages": 55, "vulnerabilities": 0, "adverse_statuses": 0}
    summary[field] = value
    path = _report(tmp_path / "audit.json", summary=summary)
    with pytest.raises(supply_chain.SupplyChainError, match="nonnegative integer"):
        supply_chain.validate_audit(path)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "summary": {
                "audited_packages": 0,
                "vulnerabilities": 0,
                "adverse_statuses": 0,
            }
        },
        {"summary": {}},
        {"summary": None},
        {"schema": {"version": "unexpected"}},
        {"schema": None},
        {"vulnerabilities": None},
        {"adverse_statuses": {}},
    ],
)
def test_empty_partial_and_unknown_schema_reports_cannot_claim_a_pass(
    tmp_path, changes
):
    path = _report(tmp_path / "audit.json", **changes)
    with pytest.raises(supply_chain.SupplyChainError):
        supply_chain.validate_audit(path)


@pytest.mark.parametrize("contents", ["", "[invalid", "null", "[]", "{}"])
def test_malformed_reports_fail_with_an_actionable_error_and_nonzero_cli(
    tmp_path, contents, capsys
):
    path = tmp_path / "audit.json"
    path.write_text(contents)
    assert supply_chain.main(["audit-report", str(path)]) == 1
    assert "supply chain:" in capsys.readouterr().err


def test_missing_report_fails_closed(tmp_path, capsys):
    path = tmp_path / "missing.json"
    assert supply_chain.main(["audit-report", str(path)]) == 1
    assert "cannot read audit report" in capsys.readouterr().err
