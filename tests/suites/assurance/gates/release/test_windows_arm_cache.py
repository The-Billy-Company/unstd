"""Default-branch warmups must populate the exact authenticated release cache."""

from __future__ import annotations

import re
import tomllib

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend


@pytest.fixture(scope="module")
def workflows():
    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    return {
        name: yaml.load((REPO_ROOT / f".github/workflows/{name}.yml").read_text())
        for name in ("release", "windows-arm-cache")
    }


def _setup(job):
    return next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )


def _build(job):
    return next(step for step in job["steps"] if "CIBW_BUILD" in step.get("env", {}))


def test_arm_warmup_runs_on_main_before_tags_need_its_cache(workflows):
    warmup = workflows["windows-arm-cache"]
    assert warmup["on"]["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in warmup["on"]
    assert warmup["on"]["schedule"] == [{"cron": "23 13 * * 1,4"}]
    assert {
        "pyproject.toml",
        "uv.lock",
        "tests/tooling/release/cache_key.py",
        "tests/tooling/release/wheels.py",
    } <= set(warmup["on"]["push"]["paths"])
    assert warmup["permissions"] == {"contents": "read"}
    assert not {"pull_request_target", "workflow_run"} & warmup["on"].keys()
    job = warmup["jobs"]["dependencies"]
    assert job["runs-on"] == "windows-11-arm"
    versions = ["312", "313", "314", "315"]
    assert job["strategy"]["matrix"]["python"] == versions
    assert (
        workflows["release"]["jobs"]["wheels"]["strategy"]["matrix"]["python"]
        == versions
    )
    assert job["strategy"]["fail-fast"] is False
    assert 0 < job["timeout-minutes"] <= 15
    for step in job["steps"]:
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}$", step["uses"])


def test_release_and_warmup_cannot_select_stock_runner_interpreters(workflows):
    for workflow in workflows.values():
        assert workflow["env"]["UV_MANAGED_PYTHON"] == "1"
        for job in workflow["jobs"].values():
            assert job.get("env", {}).get("UV_MANAGED_PYTHON", "1") == "1"


def test_warmup_and_release_share_cache_keys_and_tool_downloads(workflows):
    release = workflows["release"]["jobs"]["wheels"]
    warmup = workflows["windows-arm-cache"]["jobs"]["dependencies"]
    settings = _setup(release)["with"]
    warm_settings = _setup(warmup)["with"]
    assert warm_settings == {
        **settings,
        "cache-suffix": settings["cache-suffix"].replace(
            "${{ matrix.os }}", "windows-11-arm"
        ),
    }
    assert settings["enable-cache"] is True
    assert settings["cache-dependency-glob"] == ""
    assert settings["prune-cache"] is False
    assert "${{ matrix.python }}" in settings["cache-suffix"]
    assert "${{ steps.dependencies.outputs.key }}" in settings["cache-suffix"]
    for job in (release, warmup):
        key = next(step for step in job["steps"] if step.get("id") == "dependencies")
        assert key["run"] == "python tests/tooling/release/cache_key.py"
        assert job["steps"].index(key) < job["steps"].index(_setup(job))
        assert _build(job)["env"]["CIBW_CACHE_PATH"] == (
            "${{ env.UV_CACHE_DIR }}/cibuildwheel"
        )


def test_warmup_uses_the_release_hash_checks_and_cannot_skip_release_tests(workflows):
    release = _build(workflows["release"]["jobs"]["wheels"])
    warmup = _build(workflows["windows-arm-cache"]["jobs"]["dependencies"])
    for name in (
        "CIBW_BUILD",
        "CIBW_BEFORE_BUILD",
        "CIBW_BUILD_FRONTEND",
        "CIBW_DEPENDENCY_VERSIONS",
        "CIBW_TEST_EXTRAS",
        "CIBW_TEST_REQUIRES",
        "CIBW_BEFORE_TEST",
    ):
        assert warmup["env"][name] == release["env"][name]
    assert "CIBW_TEST_COMMAND" not in release["env"]
    assert "CIBW_TEST_SKIP" not in release["env"]
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert project["tool"]["cibuildwheel"]["test-command"][-1] == (
        "python {project}/tests/tooling/release/wheel_tests.py --workers 4"
    )
