"""Revision comparisons must measure isolated products with the same instrument."""

from __future__ import annotations

import re
import shlex
from typing import Any

import pytest

from tests.support.anchors import REPO_ROOT
from tests.support.backends import require_backend


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    yaml = require_backend("ruamel.yaml")
    loaded: object = yaml.YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".github/workflows/performance.yml").read_text()
    )
    assert isinstance(loaded, dict)
    return loaded


def _measurement_steps(workflow: dict[str, Any]) -> dict[str, Any]:
    return {
        shlex.split(step["run"])[3]: step
        for step in workflow["jobs"]["compare"]["steps"]
        if "-m bench.speeds" in step.get("run", "")
    }


def test_performance_runs_on_product_changes_and_explicit_reference(
    workflow: dict[str, Any],
) -> None:
    assert {"pull_request", "workflow_dispatch"} <= workflow["on"].keys()
    assert "src/unstd/**" in workflow["on"]["pull_request"]["paths"]
    base = workflow["on"]["workflow_dispatch"]["inputs"]["base_ref"]
    assert base["required"] is True
    assert base["type"] == "string"
    comparison = next(
        step
        for step in workflow["jobs"]["compare"]["steps"]
        if step.get("with", {}).get("path") == ".local/performance/source-before"
    )
    assert comparison["with"]["ref"] == (
        "${{ github.event.pull_request.base.sha || inputs.base_ref }}"
    )


def test_comparison_uses_read_only_permissions_pinned_tools_and_hard_failures(
    workflow: dict[str, Any],
) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"compare"}
    job = workflow["jobs"]["compare"]
    assert 1 <= job["timeout-minutes"] <= 20
    assert not job.get("continue-on-error", False)
    assert job["env"]["CIBUILDWHEEL"] == "1"
    assert job["env"]["PYTHONHASHSEED"] == "0"
    for step in job["steps"]:
        assert not step.get("continue-on-error", False)
        if "uses" in step:
            assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


def test_revisions_build_their_own_native_modules_into_separate_installations(
    workflow: dict[str, Any],
) -> None:
    steps = workflow["jobs"]["compare"]["steps"]
    sync = next(step["run"] for step in steps if "uv sync" in step.get("run", ""))
    assert {"--locked", "--all-extras", "--no-install-project"} <= set(
        shlex.split(sync)
    )
    builds = next(
        step["run"] for step in steps if "uv pip install" in step.get("run", "")
    ).splitlines()
    assert len(builds) == 2
    targets = []
    for command in builds:
        tokens = shlex.split(command)
        assert {"--no-deps", "--no-build-isolation", "--no-cache"} <= set(tokens)
        targets.append(tokens[tokens.index("--target") + 1])
    assert len(set(targets)) == 2
    assert shlex.split(builds[0])[-1] == ".local/performance/source-before"
    assert shlex.split(builds[1])[-1] == "."
    isolation = next(
        step["run"] for step in steps if "PERF_INSTALL_ROOT" in step.get("run", "")
    )
    assert "for revision in before current" in isolation
    assert "Path(unstd.__file__).resolve().parent != expected.resolve()" in isolation
    assert "Path(bench.speeds.__file__).resolve() != harness.resolve()" in isolation


def test_one_worker_baseline_and_harness_judge_both_revisions(
    workflow: dict[str, Any],
) -> None:
    measurements = _measurement_steps(workflow)
    assert list(measurements) == ["freeze", "verify"]
    baseline_paths = []
    for verb, step in measurements.items():
        tokens = shlex.split(step["run"])
        assert tokens[:4] == [".venv/bin/python", "-m", "bench.speeds", verb]
        assert int(tokens[tokens.index("--rounds") + 1]) >= 7
        baseline_paths.append(tokens[tokens.index("--baseline") + 1])
        assert "--output" in tokens
    assert len(set(baseline_paths)) == 1
    assert baseline_paths[0] != "bench/speeds.json"
    assert "installed-before:" in measurements["freeze"]["env"]["PYTHONPATH"]
    assert "installed-current:" in measurements["verify"]["env"]["PYTHONPATH"]
    for step in measurements.values():
        assert step["env"]["PYTHONPATH"].endswith(":${{ github.workspace }}")


def test_comparison_retains_evidence_on_regression(workflow: dict[str, Any]) -> None:
    upload = next(
        step
        for step in workflow["jobs"]["compare"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    assert upload["with"]["include-hidden-files"] is True
    assert upload["with"]["if-no-files-found"] == "error"
    path = upload["with"]["path"].rstrip("/")
    for step in _measurement_steps(workflow).values():
        tokens = shlex.split(step["run"])
        assert tokens[tokens.index("--output") + 1].startswith(path + "/")
        assert tokens[tokens.index("--baseline") + 1].startswith(path + "/")
