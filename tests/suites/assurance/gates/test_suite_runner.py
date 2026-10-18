"""Inventory drift and unimplemented lanes must never masquerade as test passes."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.tooling import runner


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'markers = ["unit: unit evidence", "chaos: faults", "concurrency: histories", "perf: timing"]\n',
        encoding="utf-8",
    )
    (tmp_path / "tests/tooling").mkdir(parents=True)
    path = tmp_path / "tests/suites/product/serde/unit"
    path.mkdir(parents=True)
    (path / "test_contract.py").write_text(
        "def test_contract(): pass\n", encoding="utf-8"
    )
    _inventory(tmp_path)
    return tmp_path


def _row(**changes: object) -> dict[str, object]:
    return {
        "id": "unit",
        "status": "active",
        "description": "Public behavior",
        "paths": ["tests/suites/product/serde/unit"],
        "marker": "unit",
    } | changes


def _inventory(root: Path, *rows: dict[str, object]) -> None:
    records = rows or (_row(),)
    text = "version = 1\n" + "".join(
        "\n[[suite]]\n"
        + "".join(f"{key} = {json.dumps(value)}\n" for key, value in row.items())
        for row in records
    )
    (root / "tests/tooling/inventory.toml").write_text(text, encoding="utf-8")


def test_active_suite_is_validated_and_listed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suites = runner.check_inventory(repo)
    assert [suite.id for suite in suites] == ["unit"]
    assert runner.main(["list"], repo_root=repo) == 0
    assert "unit\tactive\tPublic behavior" in capsys.readouterr().out
    assert runner.main(["check"], repo_root=repo) == 0
    assert "1 active, 0 planned" in capsys.readouterr().out


def test_unregistered_test_is_inventory_drift(repo: Path) -> None:
    (repo / "tests/suites/test_unowned.py").touch()
    with pytest.raises(runner.InventoryError, match=r"no suite owner.*test_unowned"):
        runner.check_inventory(repo)


@pytest.mark.parametrize(
    "name", ["test_stray.py", "support/test_helper.py", "tooling/test_harness.py"]
)
def test_tests_outside_suite_discovery_are_inventory_drift(
    repo: Path, name: str
) -> None:
    stray = repo / "tests" / name
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.touch()
    with pytest.raises(runner.InventoryError, match="no suite owner"):
        runner.check_inventory(repo)


def test_pycache_outside_suites_is_not_inventory_drift(repo: Path) -> None:
    cache = repo / "tests/tooling/__pycache__"
    cache.mkdir()
    (cache / "test_stale.py").touch()
    assert len(runner.check_inventory(repo)) == 1


@pytest.mark.parametrize("nested", [False, True])
def test_overlapping_paths_cannot_claim_the_same_tests(
    repo: Path, *, nested: bool
) -> None:
    path = "tests/suites/product" if nested else "tests/suites/product/serde/unit"
    _inventory(repo, _row(), _row(id="second", paths=[path]))
    with pytest.raises(runner.InventoryError, match="overlapping suite paths"):
        runner.check_inventory(repo)


def test_duplicate_paths_in_one_suite_are_rejected(repo: Path) -> None:
    _inventory(repo, _row(paths=["tests/suites/product/serde/unit"] * 2))
    with pytest.raises(runner.InventoryError, match="overlapping suite paths"):
        runner.check_inventory(repo)


def test_duplicate_suite_ids_are_rejected(repo: Path) -> None:
    _inventory(repo, _row(), _row())
    with pytest.raises(runner.InventoryError, match="duplicate suite id"):
        runner.check_inventory(repo)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "implemented"}, "status must be active or planned"),
        ({"status": 1}, "status must be a nonempty string"),
        ({"marker": "unknown"}, "unregistered pytest marker"),
        ({"paths": []}, "paths must be a nonempty list"),
        ({"paths": [1]}, "paths must contain strings"),
        ({"description": " "}, "description must be a nonempty string"),
    ],
)
def test_invalid_suite_records_are_rejected(
    repo: Path, changes: dict[str, object], message: str
) -> None:
    _inventory(repo, _row(**changes))
    with pytest.raises(runner.InventoryError, match=message):
        runner.check_inventory(repo)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/outside",
        "tests/suites/../../src",
        "tests/support",
        "tests//suites/product",
        "tests/suites/./product",
        "tests\\suites\\product",
    ],
)
def test_paths_cannot_escape_the_suite_tree(repo: Path, path: str) -> None:
    _inventory(repo, _row(paths=[path]))
    with pytest.raises(runner.InventoryError, match="beneath tests/suites"):
        runner.check_inventory(repo)


def test_symlink_cannot_escape_the_suite_tree(repo: Path) -> None:
    outside = repo / "elsewhere"
    outside.mkdir()
    (repo / "tests/suites/escape").symlink_to(outside, target_is_directory=True)
    _inventory(repo, _row(paths=["tests/suites/escape"]))
    with pytest.raises(runner.InventoryError, match="escapes tests/suites"):
        runner.check_inventory(repo)


def test_test_file_symlink_cannot_escape_its_owner(repo: Path) -> None:
    outside = repo / "test_outside.py"
    outside.touch()
    (repo / "tests/suites/product/serde/unit/test_escape.py").symlink_to(outside)
    with pytest.raises(runner.InventoryError, match="test file escapes its suite"):
        runner.check_inventory(repo)


@pytest.mark.parametrize("status", ["active", "planned"])
def test_missing_directories_are_rejected(repo: Path, status: str) -> None:
    _inventory(repo, _row(status=status, paths=["tests/suites/absent"]))
    with pytest.raises(runner.InventoryError, match="missing suite directory"):
        runner.check_inventory(repo)


def test_active_directory_requires_executable_test_files(repo: Path) -> None:
    (repo / "tests/suites/product/serde/unit/test_contract.py").unlink()
    (repo / "tests/suites/product/serde/unit/README.md").touch()
    with pytest.raises(runner.InventoryError, match="active path contains no test_"):
        runner.check_inventory(repo)


def test_pycache_does_not_satisfy_an_active_suite(repo: Path) -> None:
    path = repo / "tests/suites/product/serde/unit"
    (path / "test_contract.py").unlink()
    (path / "__pycache__").mkdir()
    (path / "__pycache__/test_stale.py").touch()
    with pytest.raises(runner.InventoryError, match="active path contains no test_"):
        runner.check_inventory(repo)


def test_planned_suite_is_listed_but_cannot_pass_a_run(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = repo / "tests/suites/assurance/adversarial/chaos"
    path.mkdir(parents=True)
    (path / "README.md").touch()
    _inventory(
        repo,
        _row(),
        _row(
            id="chaos",
            status="planned",
            paths=["tests/suites/assurance/adversarial/chaos"],
            marker="chaos",
        ),
    )
    assert len(runner.check_inventory(repo)) == 2
    assert runner.main(["list"], repo_root=repo) == 0
    assert "chaos\tplanned" in capsys.readouterr().out
    assert runner.main(["run", "chaos"], repo_root=repo) == 2
    assert "is planned; no executable coverage is claimed" in capsys.readouterr().err


def test_planned_suite_with_tests_must_be_activated(repo: Path) -> None:
    path = repo / "tests/suites/assurance/adversarial/chaos"
    path.mkdir(parents=True)
    (path / "test_faults.py").touch()
    _inventory(
        repo,
        _row(),
        _row(
            id="chaos",
            status="planned",
            paths=["tests/suites/assurance/adversarial/chaos"],
            marker="chaos",
        ),
    )
    with pytest.raises(runner.InventoryError, match="planned path contains test_"):
        runner.check_inventory(repo)


@pytest.mark.parametrize(
    ("marker", "selection"),
    [
        ("unit", "unit and not perf and not chaos and not concurrency"),
        ("perf", "perf"),
        ("concurrency", "concurrency"),
        ("chaos", "chaos"),
    ],
)
def test_run_preserves_pytest_exit_arguments_and_lane_selection(
    repo: Path, monkeypatch: pytest.MonkeyPatch, marker: str, selection: str
) -> None:
    _inventory(repo, _row(id=marker, marker=marker))
    calls: list[tuple[list[str], Path, bool]] = []

    def execute(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd, check))
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(subprocess, "run", execute)
    assert runner.main(["run", marker, "--", "-k", "a or b", "-q"], repo_root=repo) == 5
    assert calls == [
        (
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/suites/product/serde/unit",
                "-m",
                selection,
                "-k",
                "a or b",
                "-q",
            ],
            repo,
            False,
        )
    ]


def test_explicit_pytest_marker_override_remains_last(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def execute(
        command: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", execute)
    assert runner.main(["run", "unit", "--", "-m", "unit or perf"], repo_root=repo) == 0
    assert calls[0][-4:] == [
        "-m",
        "unit and not perf and not chaos and not concurrency",
        "-m",
        "unit or perf",
    ]


def test_run_refuses_inventory_drift_before_starting_pytest(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("pytest must not start before inventory validation")

    monkeypatch.setattr(subprocess, "run", unexpected)
    (repo / "tests/suites/test_unowned.py").touch()
    assert runner.main(["run", "unit"], repo_root=repo) == 2
    assert "no suite owner" in capsys.readouterr().err


@pytest.mark.parametrize(
    "contents", ["not TOML", "version = 2\nsuite = []", "version = true\nsuite = []"]
)
def test_malformed_inventory_is_an_actionable_failure(
    repo: Path, contents: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "tests/tooling/inventory.toml").write_text(contents, encoding="utf-8")
    assert runner.main(["check"], repo_root=repo) == 2
    assert "suite inventory:" in capsys.readouterr().err


def test_missing_inventory_is_an_actionable_failure(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "tests/tooling/inventory.toml").unlink()
    assert runner.main(["check"], repo_root=repo) == 2
    assert "cannot read" in capsys.readouterr().err


def test_missing_pytest_configuration_is_an_actionable_failure(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'fixture'\n", encoding="utf-8"
    )
    assert runner.main(["run", "unit"], repo_root=repo) == 2
    assert "must register pytest markers" in capsys.readouterr().err


def test_unknown_suite_is_an_actionable_failure(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert runner.main(["run", "missing"], repo_root=repo) == 2
    assert "unknown suite" in capsys.readouterr().err
