"""CI discovers all chaos groups and preserves evidence from bounded workers."""

from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

from tests.support.backends import require_backend
from tests.tooling.chaos import execution


REPO = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def workflow():
    yaml = require_backend("ruamel.yaml")
    return yaml.YAML(typ="safe", pure=True).load(
        (REPO / ".github/workflows/chaos.yml").read_text()
    )


def test_required_and_scheduled_chaos_use_pinned_actions_and_read_only_permissions(
    workflow,
):
    assert {"pull_request", "push", "workflow_dispatch", "schedule"} <= workflow[
        "on"
    ].keys()
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["on"]["schedule"][0]["cron"]
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert type(job["timeout-minutes"]) is int
        assert 1 <= job["timeout-minutes"] <= 65
        assert not job.get("continue-on-error", False)
        for step in job["steps"]:
            assert step["name"]
            assert not step.get("continue-on-error", False)
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_groups_share_four_backend_platform_jobs_and_cached_setup(workflow):
    assert set(workflow["jobs"]) == {"campaign"}
    campaign = workflow["jobs"]["campaign"]
    assert "needs" not in campaign
    assert (
        campaign["name"]
        == "Fault injection / ${{ matrix.platform }} / ${{ matrix.backend }}"
    )
    assert campaign["runs-on"] == "${{ matrix.os }}"
    assert campaign["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest"],
        "backend": ["installed", "stdlib"],
        "include": [
            {"os": "ubuntu-latest", "platform": "Linux"},
            {"os": "macos-latest", "platform": "macOS"},
        ],
    }
    assert campaign["strategy"]["fail-fast"] is False
    assert campaign["env"]["UNSTD_CAMPAIGN_REPORTING"] == "1"
    setup = next(
        step
        for step in campaign["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup["with"]["enable-cache"] is True
    assert set(setup["with"]["cache-dependency-glob"].splitlines()) == {
        "uv.lock",
        "pyproject.toml",
    }
    sync = next(
        step["run"]
        for step in campaign["steps"]
        if step.get("run", "").startswith("uv sync")
    )
    assert "--locked" in sync
    assert "--all-extras" in sync
    assert "--group dev" in sync
    assert "--python 3.13" in sync
    assert "--reinstall-package unstd" in sync


def test_pull_requests_and_nightly_runs_have_explicit_round_and_resource_bounds(
    workflow,
):
    campaign = workflow["jobs"]["campaign"]
    commands = [
        step["run"]
        for step in campaign["steps"]
        if "tests.tooling.chaos run" in step.get("run", "")
    ]
    assert len(commands) == 1
    command = commands[0]
    assert "--group all" in command
    assert "--backend ${{ matrix.backend }}" in command
    command = command.replace("${{ matrix.backend }}", "backend")
    tokens = shlex.split(command)
    assert tokens[:7] == [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "tests.tooling.chaos",
        "run",
    ]
    flags = tokens[7:]
    assert len(flags) % 2 == 0
    options = dict(zip(flags[::2], flags[1::2], strict=True))
    assert options == {
        "--group": "all",
        "--backend": "backend",
        "--seed": "173",
        "--rounds": "$ROUNDS",
        "--wall-seconds": "120",
        "--per-test-seconds": "45",
        "--cpu-seconds": "60",
        "--memory-mb": "2048",
        "--artifact-dir": ".local/chaos/",
    }
    assert (
        campaign["env"]["ROUNDS"]
        == "${{ github.event_name == 'schedule' && '10' || '1' }}"
    )
    assert (
        campaign["timeout-minutes"] * 60 > len(execution.discover("chaos")) * 120 * 10
    )


def test_failed_campaigns_always_retain_hidden_observations_and_replay_reports(
    workflow,
):
    upload = next(
        step
        for step in workflow["jobs"]["campaign"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["path"] == ".local/chaos/"
    assert settings["include-hidden-files"] is True
    assert settings["if-no-files-found"] == "error"
    assert settings["retention-days"] == 14
    for variable in ("backend", "os"):
        assert f"${{{{ matrix.{variable} }}}}" in settings["name"]
    assert "${{ github.run_attempt }}" in settings["name"]
