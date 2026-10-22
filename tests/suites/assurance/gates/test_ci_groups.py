"""CI groups cover each default owner once and retain honest failure evidence."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.ci import groups as ci
from tests.tooling.ci.sharding import parse_shard, selected_nodeids
from tests.tooling.suites import runner


_OWNERS = {
    "api": {"unit", "regression"},
    "contracts": {
        "parity",
        "properties",
        "security-contracts",
        "security-structure",
        "integration",
        "pentest",
    },
    "tooling": {"gates", "fuzz-replay"},
    "packaging": {"packaging"},
}


def _add_suite(root: Path, identity: str, *, status: str = "active") -> None:
    path = f"tests/suites/{identity}"
    directory = root / path
    directory.mkdir(parents=True)
    if status == "active":
        (directory / "test_contract.py").write_text("def test_contract(): pass\n")
    row = {
        "id": identity,
        "status": status,
        "description": "An owned contract",
        "paths": [path],
        # Deliberately shared: CI must partition owners, not overlapping markers.
        "marker": "unit",
    }
    with (root / "tests/tooling/suites/inventory.toml").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("\n[[suite]]\n")
        stream.writelines(
            f"{key} = {json.dumps(value)}\n" for key, value in row.items()
        )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["unit: contracts"]\n'
        'addopts = "--import-mode=importlib"\n'
    )
    (tmp_path / "tests/tooling/suites").mkdir(parents=True)
    (tmp_path / "tests/tooling/suites/inventory.toml").write_text("version = 1\n")
    for identity in sorted(set.union(*_OWNERS.values()) | ci.OPT_IN_SUITES):
        _add_suite(tmp_path, identity)
    _add_suite(tmp_path, "system-integration", status="planned")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    return tmp_path


def _write_report(path: Path, *, failures: int = 0) -> None:
    path.write_text(
        '<testsuites><testsuite tests="7" failures="'
        f'{failures}" errors="0" skipped="2" time="1.25"/></testsuites>'
    )


def test_repository_default_owners_have_exactly_one_disjoint_ci_group() -> None:
    groups = ci.check_groups(REPO_ROOT)
    assert {
        name: {suite.id for suite in suites} for name, suites in groups.items()
    } == _OWNERS
    active = {
        suite.id
        for suite in runner.check_inventory(REPO_ROOT)
        if suite.status == "active" and suite.id not in ci.OPT_IN_SUITES
    }
    assert set.union(*_OWNERS.values()) == active
    paths = [
        REPO_ROOT / path
        for suites in groups.values()
        for suite in suites
        for path in suite.paths
    ]
    assert all(
        not first.is_relative_to(second) and not second.is_relative_to(first)
        for index, first in enumerate(paths)
        for second in paths[index + 1 :]
    )


def test_check_and_list_show_human_group_names_without_opt_in_or_planned_suites(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ci.main(["check"], repo_root=repo) == 0
    assert "4 groups, 11 suites" in capsys.readouterr().out
    assert ci.main(["list"], repo_root=repo) == 0
    output = capsys.readouterr().out
    assert "api\tAPI & regressions\tunit, regression" in output
    assert not any(
        identity in output for identity in (*ci.OPT_IN_SUITES, "system-integration")
    )


def test_new_active_suite_requires_explicit_group_assignment_before_pytest_starts(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _add_suite(repo, "new-contracts")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("inventory drift launched pytest")

    monkeypatch.setattr(subprocess, "run", unexpected)
    assert ci.main(["run", "api"], repo_root=repo) == 2
    assert "active suites have no CI group: new-contracts" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("suites", "message"),
    [
        (("unit",), "multiple CI groups: unit"),
        (("chaos",), "requires an active default suite: chaos"),
        (("performance",), "requires an active default suite: performance"),
        (("missing",), "requires an active default suite: missing"),
        ((), "duplicate or empty CI group"),
    ],
)
def test_invalid_group_members_cannot_claim_passing_coverage(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    suites: tuple[str, ...],
    message: str,
) -> None:
    monkeypatch.setattr(
        ci, "GROUPS", (*ci.GROUPS, ci.Group("invalid", "Invalid", suites))
    )
    with pytest.raises(runner.InventoryError, match=message):
        ci.check_groups(repo)


def test_unowned_test_file_is_rejected_before_ci_selection(repo: Path) -> None:
    (repo / "tests/suites/test_unowned.py").touch()
    with pytest.raises(runner.InventoryError, match="no suite owner"):
        ci.check_groups(repo)


@pytest.mark.parametrize("returncode", [0, 1, 2, 5, -9])
def test_run_passes_owned_paths_and_literal_arguments_and_preserves_pytest_exit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    calls = []

    def execute(command, *, cwd, check):
        calls.append((command, cwd, check))
        _write_report(Path(command[-1].split("=", 1)[1]), failures=int(returncode == 1))
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(subprocess, "run", execute)
    assert (
        ci.main(
            ["run", "api", "--", "--require-extras", "-q", "--junitxml=ignored.xml"],
            repo_root=repo,
        )
        == returncode
    )
    command, cwd, check = calls[0]
    assert cwd == repo
    assert check is False
    assert command == [
        sys.executable,
        "-m",
        "pytest",
        "tests/suites/unit",
        "tests/suites/regression",
        "-m",
        ci.DEFAULT_SELECTION,
        "--require-extras",
        "-q",
        "--junitxml=ignored.xml",
        f"--junitxml={repo / '.local/ci/api.xml'}",
    ]
    summary = (repo / "summary.md").read_text()
    assert "API & regressions" in summary
    assert "7 tests:" in summary
    assert "1.25s" in summary
    assert ("— passed." in summary) == (returncode == 0)
    if returncode:
        assert f"failed (exit {returncode})" in summary
    if returncode == 1:
        assert "4 passed, 1 failures" in summary


@pytest.mark.parametrize(
    "contents",
    [
        None,
        "<broken",
        "<testsuites/>",
        '<testsuite tests="0" failures="0" errors="0" skipped="0" time="0"/>',
    ],
)
def test_missing_malformed_and_empty_reports_fail_even_after_zero_pytest_exit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, contents: str | None
) -> None:
    def execute(command, **kwargs):
        if contents is not None:
            Path(command[-1].split("=", 1)[1]).write_text(contents)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", execute)
    assert ci.main(["run", "api"], repo_root=repo) == 1
    summary = (repo / "summary.md").read_text()
    assert "failed" in summary
    assert "JUnit report unavailable" in summary
    assert "— passed." not in summary


def test_stale_junit_cannot_hide_collection_failure_and_log_fold_always_closes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = repo / ".local/ci/api.xml"
    report.parent.mkdir(parents=True)
    _write_report(report)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    def execute(command, **kwargs):
        assert not report.exists()
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(subprocess, "run", execute)
    assert ci.main(["run", "api"], repo_root=repo) == 5
    assert capsys.readouterr().out == "::group::API & regressions\n::endgroup::\n"
    summary = (repo / "summary.md").read_text()
    assert "exit 5" in summary
    assert "JUnit report unavailable" in summary


def test_junit_failure_counts_cannot_be_laundered_by_a_zero_exit(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def execute(command, **kwargs):
        _write_report(Path(command[-1].split("=", 1)[1]), failures=1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", execute)
    assert ci.main(["run", "api"], repo_root=repo) == 1
    assert "failed" in (repo / "summary.md").read_text()


def test_pytest_launch_failure_retains_a_summary_and_closes_github_log_group(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    def execute(*args, **kwargs):
        message = "interpreter unavailable"
        raise OSError(message)

    monkeypatch.setattr(subprocess, "run", execute)
    assert ci.main(["run", "api"], repo_root=repo) == 1
    output = capsys.readouterr()
    assert "cannot start pytest: interpreter unavailable" in output.err
    assert output.out.endswith("::endgroup::\n")
    assert "failed" in (repo / "summary.md").read_text()


def test_real_pytest_group_produces_owned_tests_and_an_accurate_summary(
    repo: Path,
) -> None:
    assert ci.main(["run", "api", "--", "-q"], repo_root=repo) == 0
    summary = (repo / "summary.md").read_text()
    assert "— passed." in summary
    assert "2 tests: 2 passed, 0 failures, 0 errors, 0 skipped" in summary
    assert (repo / ".local/ci/api.xml").is_file()


@pytest.mark.parametrize("returncode", [0, 5])
def test_existing_native_or_resource_reports_append_counts_and_preserve_failure(
    repo: Path, returncode: int
) -> None:
    report = repo / "native.xml"
    _write_report(report)
    summary = repo / "summary.md"
    summary.write_text("Prior group\n\n")
    assert (
        ci.main(
            ["report", "Native safety", "native.xml", "--exit-code", str(returncode)],
            repo_root=repo,
        )
        == returncode
    )
    text = summary.read_text()
    assert text.startswith("Prior group\n\n")
    assert "Native safety" in text
    assert "7 tests: 5 passed" in text
    assert ("— passed." in text) == (returncode == 0)


@pytest.mark.parametrize("count", [1, 2, 3, 7])
def test_shards_are_order_independent_disjoint_and_complete(count):
    identities = [f"test_example.py::test_value[{number}]" for number in range(29)]
    partitions = [
        selected_nodeids(identities, (index, count)) for index in range(1, count + 1)
    ]
    assert set.union(*partitions) == set(identities)
    assert sum(map(len, partitions)) == len(identities)
    assert max(map(len, partitions)) - min(map(len, partitions)) <= 1
    assert partitions == [
        selected_nodeids(list(reversed(identities)), (index, count))
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("value", ["0/3", "4/3", "1/0", "-1/3", "1", "1/2/3", "a/3"])
def test_invalid_shard_selection_cannot_start_a_subset(value):
    with pytest.raises(ValueError, match="INDEX/TOTAL"):
        parse_shard(value)


def test_duplicate_identities_cannot_hide_overlapping_tests():
    with pytest.raises(pytest.UsageError, match="duplicate test identities"):
        selected_nodeids(["test_same", "test_same"], (1, 2))


def test_real_group_shards_execute_the_complete_selection_and_keep_failures(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    for identity in ("unit", "regression"):
        path = repo / "tests/suites" / identity / "test_contract.py"
        with path.open("a", encoding="utf-8") as stream:
            for number in range(7):
                stream.write(f"def test_value_{number}(): pass\n")
    with (repo / "tests/suites/unit/test_contract.py").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("def test_failure(): assert False, 'fixture regression'\n")
    assert ci.main(["run", "api", "--", "-q"], repo_root=repo) == 1

    def identities(path):
        root = ElementTree.parse(path).getroot()  # noqa: S314 — local pytest output
        return {
            (case.attrib["classname"], case.attrib["name"])
            for case in root.iter("testcase")
        }

    expected = identities(repo / ".local/ci/api.xml")
    selected = []
    outcomes = []
    for index in range(1, 4):
        outcomes.append(
            ci.main(["run", "--shard", f"{index}/3", "api", "--", "-q"], repo_root=repo)
        )
        selected.append(identities(repo / f".local/ci/api-{index}-of-3.xml"))
    assert sorted(outcomes) == [0, 0, 1]
    assert len(expected) == 17
    assert set.union(*selected) == expected
    assert sum(map(len, selected)) == len(expected)
