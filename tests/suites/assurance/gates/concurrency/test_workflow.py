"""CI runs every executable concurrency group with bounded retained evidence."""

from __future__ import annotations

import re
import shlex

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend
from tests.tooling.chaos import execution


@pytest.fixture(scope="module")
def workflow():
    yaml = require_backend("ruamel.yaml")
    return yaml.YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".github/workflows/concurrency.yml").read_text()
    )


def test_required_and_nightly_runs_have_pinned_actions_read_only_permissions_and_no_soft_failure(
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


def test_groups_share_four_backend_platform_jobs_and_cached_setup(
    workflow,
):
    assert set(workflow["jobs"]) == {"campaign"}
    campaign = workflow["jobs"]["campaign"]
    assert "needs" not in campaign
    assert (
        campaign["name"]
        == "Concurrency / ${{ matrix.platform }} / ${{ matrix.backend }}"
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
    assert setup["with"]["python-version"] == "3.14"
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
    for requirement in (
        "--locked",
        "--all-extras",
        "--group dev",
        "--python 3.14",
        "--reinstall-package unstd",
    ):
        assert requirement in sync


def test_pr_and_nightly_commands_bound_every_round_and_preserve_all_hidden_reports(
    workflow,
):
    campaign = workflow["jobs"]["campaign"]
    commands = [
        step["run"]
        for step in campaign["steps"]
        if "tests.tooling.concurrency run" in step.get("run", "")
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
        "tests.tooling.concurrency",
        "run",
    ]
    flags = tokens[7:]
    assert len(flags) % 2 == 0
    assert dict(zip(flags[::2], flags[1::2], strict=True)) == {
        "--group": "all",
        "--backend": "backend",
        "--seed": "173",
        "--rounds": "$ROUNDS",
        "--wall-seconds": "120",
        "--per-test-seconds": "45",
        "--cpu-seconds": "60",
        "--memory-mb": "2048",
        "--artifact-dir": ".local/concurrency/",
    }
    assert (
        campaign["env"]["ROUNDS"]
        == "${{ github.event_name == 'schedule' && '10' || '1' }}"
    )
    assert (
        campaign["timeout-minutes"] * 60
        > len(execution.discover("concurrency")) * 120 * 10
    )
    upload = next(
        step
        for step in campaign["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["path"] == ".local/concurrency/"
    assert settings["include-hidden-files"] is True
    assert settings["if-no-files-found"] == "error"
    assert settings["retention-days"] == 14
    for variable in ("backend", "os"):
        assert f"${{{{ matrix.{variable} }}}}" in settings["name"]
    assert "${{ github.run_attempt }}" in settings["name"]
