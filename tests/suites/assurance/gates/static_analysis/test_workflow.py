"""Full-source analysis must fail CI on findings and broken scanner executions."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def workflows():
    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    return {
        name: yaml.load((REPO_ROOT / f".github/workflows/{name}.yml").read_text())
        for name in ("static-analysis", "ci", "release")
    }


def _steps(workflow):
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _scanner_step(workflow, scanner):
    return next(
        step
        for step in _steps(workflow)
        if re.search(rf"\buv run --no-sync {scanner}\b", step.get("run", ""))
    )


def test_reusable_scheduled_gate_is_bounded_and_uses_pinned_actions(workflows):
    workflow = workflows["static-analysis"]
    assert {"workflow_call", "workflow_dispatch", "schedule"} <= workflow["on"].keys()
    cron = workflow["on"]["schedule"][0]["cron"].split()
    assert len(cron) == 5
    assert cron[2:4] == ["*", "*"]
    assert cron[4] in {"*", "0", "1", "2", "3", "4", "5", "6", "7"}
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert 1 <= job["timeout-minutes"] <= 40
        assert not job.get("continue-on-error", False)
        for step in job["steps"]:
            assert not step.get("continue-on-error", False)
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
                if step["uses"].startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False


@pytest.mark.parametrize("scanner", ["bandit", "pylint"])
@pytest.mark.parametrize("exit_code", [1, 2])
def test_findings_and_scanner_failures_keep_the_actual_shell_step_red(
    workflows, tmp_path: Path, scanner: str, exit_code: int
):
    step = _scanner_step(workflows["static-analysis"], scanner)
    called = tmp_path / "scanner-called"
    executable = tmp_path / "uv"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" > "$SAST_SCANNER_CALLED"\n'
        "printf 'seed scanner output\\n'\n"
        f"exit {exit_code}\n"
    )
    executable.chmod(0o700)
    (tmp_path / ".local/sast").mkdir(parents=True)
    # GitHub uses -eo pipefail for an explicit bash shell. Its default Linux
    # shell uses -e; model the step's declared policy so a missing pipefail is
    # observable when a scanner is piped through tee.
    shell = ["/bin/bash", "-e"]
    if step.get("shell") == "bash":
        shell.extend(("-o", "pipefail"))
    result = subprocess.run(  # noqa: S603 — fixed workflow with a fixture-owned scanner
        [*shell, "-c", step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SAST_SCANNER_CALLED": str(called),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert shlex.split(called.read_text())[:3] == ["run", "--no-sync", scanner]
    assert result.returncode == exit_code, result.stdout + result.stderr


def test_python_scans_cover_full_production_source_without_thresholds_or_baselines(
    workflows,
):
    workflow = workflows["static-analysis"]
    bandit = _scanner_step(workflow, "bandit")
    tokens = shlex.split(bandit["run"])
    assert {"--recursive", "src", "hatch_build.py"} <= set(tokens)
    assert tokens[tokens.index("--severity-level") + 1] == "all"
    assert tokens[tokens.index("--confidence-level") + 1] == "all"
    assert tokens[tokens.index("--format") + 1] == "json"
    assert not {"--exit-zero", "--baseline", "--exclude", "--skip", "-s", "-x"} & set(
        tokens
    )
    assert "if" not in bandit
    duplicate = _scanner_step(workflow, "pylint")
    assert shlex.split(duplicate["run"])[:5] == [
        "uv",
        "run",
        "--no-sync",
        "pylint",
        "src/unstd",
    ]
    assert "if" not in duplicate


def test_codeql_covers_every_language_with_extended_full_source_queries(workflows):
    job = workflows["static-analysis"]["jobs"]["codeql"]
    matrix = job["strategy"]["matrix"]["include"]
    assert {row["language"]: row["build-mode"] for row in matrix} == {
        "python": "none",
        "c-cpp": "manual",
        "rust": "none",
        "actions": "none",
    }
    assert job["strategy"]["fail-fast"] is False
    assert job["permissions"] == {"contents": "read", "security-events": "write"}
    init = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("github/codeql-action/init@")
    )
    assert init["with"]["languages"] == "${{ matrix.language }}"
    assert init["with"]["build-mode"] == "${{ matrix.build-mode }}"
    assert init["with"]["queries"] == "security-extended"
    assert not {"config-file", "config", "packs"} & init["with"].keys()
    analyze = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("github/codeql-action/analyze@")
    )
    # Existing GitHub default setup owns uploads; the required artifact gate
    # adds extended queries without an upload conflict or a coverage gap.
    assert analyze["with"]["upload"] == "never"
    assert analyze["with"]["upload-database"] is False
    policy = next(
        step
        for step in job["steps"]
        if "tests.tooling.static_analysis sarif" in step.get("run", "")
    )
    assert "always()" in policy["if"]
    assert (
        job["steps"].index(init)
        < job["steps"].index(analyze)
        < job["steps"].index(policy)
    )
    assert "baseline" not in policy["run"]


def test_c_analysis_builds_both_extension_implementations_with_required_compilers(
    workflows,
    tmp_path,
):
    job = workflows["static-analysis"]["jobs"]["codeql"]
    builds = [step for step in job["steps"] if "uv build " in step.get("run", "")]
    assert len(builds) == 1
    build = builds[0]
    assert build["if"] == "matrix.language == 'c-cpp'"
    assert build["env"]["CIBUILDWHEEL"] == "1"
    assert build["shell"] == "bash"
    # Exercise the actual loop and environment expansion. A static check for
    # three version strings could pass while every compile used one interpreter.
    trace = tmp_path / "builds.jsonl"
    executable = tmp_path / "uv"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['SAST_BUILD_TRACE'], 'a') as output:\n"
        "    output.write(json.dumps({'argv': sys.argv[1:], "
        "'venv': os.environ.get('VIRTUAL_ENV'), "
        "'rust': os.environ.get('UNSTD_TEXT_RUST', '0')}) + '\\n')\n"
    )
    executable.chmod(0o700)
    result = subprocess.run(  # noqa: S603 — repository workflow and fixture-owned build recorder
        ["/bin/bash", "-eo", "pipefail", "-c", build["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SAST_BUILD_TRACE": str(trace),
            "UNSTD_TEXT_RUST": "0",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(calls) == 6
    for call, (version, rust) in zip(
        calls,
        [
            (version, rust)
            for version in ("3.12", "3.13", "3.14")
            for rust in ("0", "1")
        ],
        strict=True,
    ):
        arguments = call["argv"]
        environment = str(tmp_path / f".local/sast/build-{version}")
        assert call["venv"] == environment
        assert call["rust"] == rust
        assert arguments[:3] == ["build", "--python", f"{environment}/bin/python"]
        assert {"--wheel", "--no-build-isolation"} <= set(arguments)
        destination = arguments[arguments.index("--out-dir") + 1]
        assert (
            destination
            == f".local/sast/wheels/{version}/{'rust' if rust == '1' else 'c'}"
        )
    setup = next(
        step for step in job["steps"] if step["name"] == "Install locked build tools"
    )
    assert job["steps"].index(setup) < job["steps"].index(build)
    assert "for python in 3.12 3.13 3.14" in setup["run"]
    assert 'UV_PROJECT_ENVIRONMENT=".local/sast/build-$python"' in setup["run"]
    assert '--python "$python"' in setup["run"]
    assert {"--locked", "--only-group", "build", "--no-install-project"} <= set(
        shlex.split(setup["run"])
    )


def test_analyzers_are_required_before_their_optional_seed_tests(workflows):
    steps = workflows["static-analysis"]["jobs"]["python"]["steps"]
    sync = next(step for step in steps if step.get("run", "").startswith("uv sync "))
    assert {"--locked", "--no-install-project"} <= set(shlex.split(sync["run"]))
    assert "--only-group static-analysis" in sync["run"]
    smoke = next(
        step
        for step in steps
        if "import bandit" in step.get("run", "") and "pylint" in step["run"]
    )
    tests = next(step for step in steps if "-m pytest" in step.get("run", ""))
    assert steps.index(sync) < steps.index(smoke) <= steps.index(tests)
    assert "if" not in smoke
    assert not smoke.get("continue-on-error", False)


def test_full_scan_evidence_is_validated_and_retained_even_on_failure(workflows):
    workflow = workflows["static-analysis"]
    bandit = _scanner_step(workflow, "bandit")
    tokens = shlex.split(bandit["run"])
    report = tokens[tokens.index("--output") + 1]
    policy = next(
        step
        for step in _steps(workflow)
        if "tests.tooling.static_analysis bandit" in step.get("run", "")
    )
    assert policy["if"] == "always()"
    assert shlex.split(policy["run"])[-2:] == ["bandit", report]
    codeql_steps = workflow["jobs"]["codeql"]["steps"]
    analyze = next(step for step in codeql_steps if step.get("id") == "analyze")
    policy = next(
        step
        for step in codeql_steps
        if "tests.tooling.static_analysis sarif" in step.get("run", "")
    )
    assert shlex.split(policy["run"])[-2:] == ["sarif", analyze["with"]["output"]]
    uploads = [
        step
        for step in _steps(workflow)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == len(workflow["jobs"])
    for upload in uploads:
        assert upload["if"] == "always()"
        assert upload["with"]["include-hidden-files"] is True
        assert upload["with"]["if-no-files-found"] == "error"
        assert 1 <= upload["with"]["retention-days"] <= 90


def test_ci_and_release_require_sast_and_delegate_code_scanning_permission(workflows):
    jobs = workflows["ci"]["jobs"]
    gate = jobs["static-analysis"]
    assert gate["uses"] == "./.github/workflows/static-analysis.yml"
    assert gate["permissions"]["security-events"] == "write"
    assert "if" not in gate
    assert not gate.get("continue-on-error", False)
    assert "static-analysis" in jobs["result"]["needs"]
    release = workflows["release"]["jobs"]["gate"]
    assert release["uses"] == "./.github/workflows/ci.yml"
    assert release["permissions"]["security-events"] == "write"
