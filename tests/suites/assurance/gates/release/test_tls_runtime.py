"""Runtime security preflight rejects vulnerable branches before TLS work starts."""

from __future__ import annotations

import json
import ssl
import subprocess
import sys

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.runtime import tls as runtime


@pytest.mark.parametrize(
    ("major", "minor", "fixed"),
    [(3, 0, 23), (3, 4, 8), (3, 5, 9), (3, 6, 5), (4, 0, 3)],
)
def test_each_branch_rejects_the_previous_patch_and_accepts_the_fix(
    major, minor, fixed
):
    for patch in (fixed - 1, fixed, fixed + 1):
        errors = runtime.openssl_policy_errors(
            f"OpenSSL {major}.{minor}.{patch} 29 Sep 2026",
            (major, minor, 0, patch, 0),
            has_tls_1_3=True,
        )
        assert bool(errors) is (patch < fixed)
        if errors:
            assert f"{major}.{minor}.{fixed}" in errors[0]
            assert runtime.ADVISORY in errors[0]


@pytest.mark.parametrize(("major", "minor", "patch"), [(3, 6, 0), (4, 0, 0)])
def test_a_numerically_newer_branch_does_not_bypass_its_own_fix(major, minor, patch):
    assert (major, minor, patch) > (3, 5, 9)
    assert runtime.openssl_policy_errors(
        f"OpenSSL {major}.{minor}.{patch}",
        (major, minor, 0, patch, 0),
        has_tls_1_3=True,
    )


@pytest.mark.parametrize(
    ("version", "info"),
    [
        ("LibreSSL 4.0.3", (4, 0, 0, 3, 0)),
        ("OpenSSL 3.5.9-beta1", (3, 5, 0, 9, 0)),
        ("OpenSSL 3.5.9-dev", (3, 5, 0, 9, 0)),
        ("OpenSSL 3.5.9", (3, 5, 9, 0, 0)),
        ("OpenSSL 3.5.9", (3, 5, 0, 7, 0)),
        ("OpenSSL 1.1.1zj", (1, 1, 1, 36, 15)),
        ("OpenSSL 3.3.99", (3, 3, 0, 99, 0)),
        ("OpenSSL 4.1.0", (4, 1, 0, 0, 0)),
    ],
)
def test_unknown_unsupported_prerelease_and_inconsistent_metadata_fail_closed(
    version, info
):
    assert runtime.openssl_policy_errors(version, info, has_tls_1_3=True)


def test_a_patched_engine_still_requires_tls_1_3():
    assert runtime.openssl_policy_errors(
        "OpenSSL 3.5.9", (3, 5, 0, 9, 0), has_tls_1_3=False
    ) == ("the interpreter must support TLS 1.3",)


@pytest.mark.parametrize(("patch", "expected"), [(7, 1), (9, 0)])
def test_cli_exit_status_and_structured_evidence_match_the_checked_stdlib_engine(
    monkeypatch, capsys, patch, expected
):
    monkeypatch.setattr(ssl, "OPENSSL_VERSION", f"OpenSSL 3.5.{patch}")
    monkeypatch.setattr(ssl, "OPENSSL_VERSION_INFO", (3, 5, 0, patch, 0))
    monkeypatch.setattr(ssl, "HAS_TLSv1_3", True)
    assert runtime.main(["--json"]) == expected
    report = json.loads(capsys.readouterr().out)
    assert report["openssl"] == f"OpenSSL 3.5.{patch}"
    assert report["openssl_version_info"] == [3, 5, 0, patch, 0]
    assert bool(report["errors"]) is bool(expected)
    assert report["executable"] == sys.executable


def test_rejection_is_actionable_without_structured_output(monkeypatch, capsys):
    monkeypatch.setattr(ssl, "OPENSSL_VERSION", "OpenSSL 3.5.7")
    monkeypatch.setattr(ssl, "OPENSSL_VERSION_INFO", (3, 5, 0, 7, 0))
    assert runtime.main([]) == 1
    captured = capsys.readouterr()
    assert "OpenSSL 3.5.7" in captured.out
    assert "upgrade the interpreter's linked OpenSSL to 3.5.9" in captured.err


def test_preflight_runs_in_an_isolated_stdlib_process_without_importing_unstd():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "tests.tooling.runtime", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert report["openssl"] == ssl.OPENSSL_VERSION
    assert result.returncode == (1 if report["errors"] else 0)
    assert not result.stderr
