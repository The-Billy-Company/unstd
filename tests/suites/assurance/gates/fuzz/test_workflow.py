"""CI discovers every target/backend pair and retains failures from bounded runs.

The independent YAML reader checks workflow semantics. The CLI matrix gate
executes the discovery command itself, so a valid YAML file cannot conceal an
empty, duplicated or hard-coded campaign matrix.
"""

from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

from tests.support.backends import require_backend


REPO = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def workflow():
    yaml = require_backend("ruamel.yaml")
    return yaml.YAML(typ="safe", pure=True).load(
        (REPO / ".github/workflows/fuzz.yml").read_text()
    )


def _arguments(step):
    command = step["run"]
    assert "--target ${{ matrix.target }}" in command
    assert "--backend ${{ matrix.backend }}" in command
    command = command.replace("${{ matrix.target }}", "target").replace(
        "${{ matrix.backend }}", "backend"
    )
    tokens = shlex.split(command)
    assert tokens[:7] == [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "tests.tooling.fuzz",
        "campaign",
    ]
    flags = tokens[7:]
    assert len(flags) % 2 == 0
    return dict(zip(flags[::2], flags[1::2], strict=True))


def test_required_and_scheduled_fuzz_jobs_use_pinned_actions_and_read_only_permissions(
    workflow,
):
    assert {"pull_request", "push", "workflow_dispatch", "schedule"} <= workflow[
        "on"
    ].keys()
    assert workflow["on"]["schedule"][0]["cron"]
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert type(job["timeout-minutes"]) is int
        assert 1 <= job["timeout-minutes"] <= 30
        assert not job.get("continue-on-error", False)
        for step in job["steps"]:
            assert not step.get("continue-on-error", False)
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])


def test_campaign_matrix_is_discovered_from_authenticated_inventory(workflow):
    inventory = workflow["jobs"]["inventory"]
    discovery = next(step for step in inventory["steps"] if step.get("id") == "targets")
    assert "python -m tests.tooling.fuzz list --json --all-backends" in discovery["run"]
    assert '"$GITHUB_OUTPUT"' in discovery["run"]
    assert inventory["outputs"]["matrix"] == "${{ steps.targets.outputs.matrix }}"
    campaign = workflow["jobs"]["campaign"]
    assert campaign["needs"] == "inventory"
    assert (
        campaign["strategy"]["matrix"]
        == "${{ fromJSON(needs.inventory.outputs.matrix) }}"
    )
    assert campaign["strategy"]["fail-fast"] is False
    sync = next(
        step["run"]
        for step in campaign["steps"]
        if step.get("run", "").startswith("uv sync")
    )
    assert "--locked" in sync
    assert "--all-extras" in sync
    assert "--group fuzz" in sync
    assert "--python 3.12" in sync


def test_both_mutation_engines_execute_with_explicit_small_and_nightly_budgets(
    workflow,
):
    campaign = workflow["jobs"]["campaign"]
    commands = [
        _arguments(step)
        for step in campaign["steps"]
        if "tests.tooling.fuzz campaign" in step.get("run", "")
    ]
    assert len(commands) == 2
    engines = {command["--engine"]: command for command in commands}
    assert set(engines) == {"deterministic", "atheris"}
    deterministic = engines["deterministic"]
    assert deterministic["--runs"] == "1000"
    assert deterministic["--wall-seconds"] == "30"
    assert deterministic["--cpu-seconds"] == "20"
    atheris = engines["atheris"]
    assert atheris["--runs"] == "$RUNS"
    assert atheris["--wall-seconds"] == "$WALL"
    assert atheris["--cpu-seconds"] == "$CPU"
    for variable, nightly, normal in (
        ("RUNS", 100000, 1000),
        ("WALL", 120, 30),
        ("CPU", 110, 20),
    ):
        assert campaign["env"][variable] == (
            "${{ github.event_name == 'schedule' && '"
            + str(nightly)
            + "' || '"
            + str(normal)
            + "' }}"
        )
    assert deterministic["--seed"] == atheris["--seed"] == "173"
    assert deterministic["--artifact-dir"] != atheris["--artifact-dir"]


def test_ci_retains_hidden_evidence_even_when_a_campaign_fails(workflow):
    steps = workflow["jobs"]["campaign"]["steps"]
    upload = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["path"] == ".local/fuzz/"
    assert settings["include-hidden-files"] is True
    assert settings["if-no-files-found"] == "error"
    assert settings["retention-days"] == 14
    assert "${{ matrix.target }}" in settings["name"]
    assert "${{ matrix.backend }}" in settings["name"]
