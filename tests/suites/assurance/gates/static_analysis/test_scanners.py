"""Real analyzers must expose independently seeded unsafe and duplicated code."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.support.anchors import REPO_ROOT


_DUPLICATE_BODY = """    output = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized = text.casefold()
        if normalized in output:
            continue
        output.append(normalized)
    return output
"""


def test_real_bandit_rejects_unsafe_deserialization_and_shell_execution(tmp_path):
    pytest.importorskip("bandit")
    source = tmp_path / "unsafe.py"
    source.write_text(
        "import pickle\nimport subprocess\n"
        "def unsafe(payload, command):\n"
        "    value = pickle.loads(payload)\n"
        "    subprocess.run(command, shell=True)\n"
        "    return value\n"
    )
    report = tmp_path / "bandit.json"
    result = subprocess.run(  # noqa: S603 — analyze fixture source; never execute it
        [
            sys.executable,
            "-m",
            "bandit",
            "--recursive",
            str(source),
            "--severity-level",
            "all",
            "--confidence-level",
            "all",
            "--format",
            "json",
            "--output",
            str(report),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(report.read_text())
    assert {"B301", "B602"} <= {row["test_id"] for row in evidence["results"]}
    assert evidence["errors"] == []
    assert evidence["metrics"]["_totals"]["loc"] > 0


def test_real_pylint_rejects_duplicated_function_bodies_using_repository_policy(
    tmp_path,
):
    pytest.importorskip("pylint")
    modules = []
    for name in ("first", "second"):
        source = tmp_path / f"{name}.py"
        source.write_text(f"def {name}(values):\n" + _DUPLICATE_BODY)
        modules.append(str(source))
    result = subprocess.run(  # noqa: S603 — repository policy analyzes fixture source
        [
            sys.executable,
            "-m",
            "pylint",
            "--rcfile",
            str(REPO_ROOT / "pyproject.toml"),
            "--reports=n",
            "--score=n",
            *modules,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 8, result.stdout + result.stderr
    assert "R0801" in result.stdout
    assert "duplicate-code" in result.stdout
