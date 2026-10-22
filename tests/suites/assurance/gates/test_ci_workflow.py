"""GitHub's grouped CI retains every environment and rejects incomplete results."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.support.anchors import REPO_ROOT
from tests.support.backends import require_backend
from tests.tooling import ci


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def workflow():
    yaml = require_backend("ruamel.yaml")
    return yaml.YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    )


def _render(command, *, group, environment):
    matrix = {"group": group, "environment": environment}
    return re.sub(
        r"\$\{\{ matrix\.(group|environment)\.([\w-]+) \}\}",
        lambda match: matrix[match[1]][match[2]],
        command,
    )


def _sync(job):
    return next(
        step["run"]
        for step in job["steps"]
        if step.get("run", "").startswith("uv sync ")
    )


def test_every_owned_group_runs_in_every_supported_dependency_environment(workflow):
    job = workflow["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    # An include-only row or exclusion could quietly leave a group untested.
    assert set(matrix) == {"group", "environment"}
    groups = matrix["group"]
    assert len(groups) == len({group.id for group in ci.GROUPS})
    assert {group["id"] for group in groups} == {group.id for group in ci.GROUPS}
    assert all(group["name"] for group in groups)
    assert "${{ matrix.group.name }}" in job["name"]
    assert "${{ matrix.environment.name }}" in job["name"]
    assert job["strategy"]["fail-fast"] is False
    environments = matrix["environment"]
    assert len(environments) == len({item["id"] for item in environments}) == 5
    contracts = set()
    for environment in environments:
        flags = shlex.split(environment["sync"])
        resolution = (
            flags[flags.index("--resolution") + 1] if "--resolution" in flags else None
        )
        contracts.add(
            (
                environment["python"],
                "--locked" in flags,
                "--all-extras" in flags,
                resolution,
                "--require-extras" in shlex.split(environment["pytest"]),
            )
        )
    assert contracts == {
        ("3.12", True, True, None, True),
        ("3.13", True, True, None, True),
        ("3.14", True, True, None, True),
        ("3.13", False, True, "lowest-direct", True),
        ("3.13", True, False, None, False),
    }


def test_test_commands_use_the_selected_group_and_preserve_its_resolved_environment(
    workflow,
):
    job = workflow["jobs"]["test"]
    commands = [
        step["run"]
        for step in job["steps"]
        if "tests.tooling.ci run " in step.get("run", "")
    ]
    assert len(commands) == 1
    for group in job["strategy"]["matrix"]["group"]:
        for environment in job["strategy"]["matrix"]["environment"]:
            tokens = shlex.split(
                _render(commands[0], group=group, environment=environment)
            )
            assert tokens[:8] == [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "tests.tooling.ci",
                "run",
                group["id"],
            ]
            assert tokens[8] == "--"
            assert ("--require-extras" in tokens[9:]) == (
                "--all-extras" in shlex.split(environment["sync"])
            )
    # A later uv invocation must not replace floors, base, or native-only extras.
    for candidate in workflow["jobs"].values():
        for step in candidate.get("steps", []):
            for line in step.get("run", "").splitlines():
                if line.strip().startswith("uv run "):
                    assert "--no-sync" in shlex.split(line)


def test_cached_dependency_setup_rebuilds_source_before_running_tests(workflow):
    for identity in ("lint", "test", "resources", "native"):
        job = workflow["jobs"][identity]
        setup_index = next(
            index
            for index, step in enumerate(job["steps"])
            if step.get("uses", "").startswith("astral-sh/setup-uv@")
        )
        setup = job["steps"][setup_index]["with"]
        assert setup["enable-cache"] in (True, "auto")
        assert set(setup["cache-dependency-glob"].splitlines()) == {
            "uv.lock",
            "pyproject.toml",
        }
        sync = _sync(job)
        assert "--reinstall-package unstd" in sync
        assert "--group dev" in sync
        assert (
            job["steps"].index(next(s for s in job["steps"] if s.get("run") == sync))
            > setup_index
        )
    # Sanitizers deliberately skip the project during sync, then rebuild it
    # with the sanitizer environment before any import or pytest execution.
    sanitizer = workflow["jobs"]["native-sanitizers"]
    assert "--no-install-project" in _sync(sanitizer)
    rebuild = next(
        index
        for index, step in enumerate(sanitizer["steps"])
        if "uv pip install " in step.get("run", "") and "--reinstall" in step["run"]
    )
    execution = next(
        index
        for index, step in enumerate(sanitizer["steps"])
        if "-m pytest " in step.get("run", "")
    )
    assert rebuild < execution


def test_only_artifact_group_primes_offline_build_dependencies(workflow):
    job = workflow["jobs"]["test"]
    builds = [
        (index, step)
        for index, step in enumerate(job["steps"])
        if step.get("run", "").startswith("uv build ")
    ]
    assert len(builds) == 1
    index, prime = builds[0]
    assert prime["if"] == "matrix.group.id == 'packaging'"
    assert {"--sdist", "--wheel"} <= set(shlex.split(prime["run"]))
    assert index < next(
        index
        for index, step in enumerate(job["steps"])
        if "tests.tooling.ci run " in step.get("run", "")
    )
    assert job["steps"][index].get("continue-on-error", False) is False


def test_release_reuses_the_complete_ci_gate_before_building_publishable_artifacts(
    workflow,
):
    yaml = require_backend("ruamel.yaml")
    release = yaml.YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".github/workflows/release.yml").read_text()
    )
    assert "workflow_call" in workflow["on"]
    jobs = release["jobs"]
    assert jobs["gate"]["uses"] == "./.github/workflows/ci.yml"
    for identity in ("wheels", "sdist"):
        needs = jobs[identity]["needs"]
        assert "gate" in ([needs] if isinstance(needs, str) else needs)


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_aggregate_gate_executes_and_rejects_any_non_success_dependency(
    workflow, tmp_path: Path, result: str
):
    jobs = workflow["jobs"]
    aggregate = jobs["result"]
    assert aggregate["if"] == "always()"
    assert set(aggregate["needs"]) == set(jobs) - {"result"}
    assert not aggregate.get("continue-on-error", False)
    (step,) = aggregate["steps"]
    assert step["env"]["RESULTS"] == "${{ toJSON(needs) }}"
    for identity in aggregate["needs"]:
        dependencies = {name: {"result": "success"} for name in aggregate["needs"]}
        # A failing matrix child is represented to dependents as a failed job.
        dependencies[identity]["result"] = result
        summary = tmp_path / f"{identity}.md"
        process = subprocess.run(  # noqa: S603 — repository-owned workflow under test
            ["/bin/bash", "-e", "-o", "pipefail", "-c", step["run"]],
            env={
                **os.environ,
                "RESULTS": json.dumps(dependencies),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert (process.returncode == 0) == (result == "success"), identity
        assert f"| {identity} | {result} |" in summary.read_text()
        if result != "success":
            assert identity in process.stderr
