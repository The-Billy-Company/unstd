"""Revision comparisons must measure isolated products with the same instrument."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
from typing import Any

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend


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
    before = shlex.split(measurements["freeze"]["run"])
    assert before[before.index("--comparison-source") + 1] == (
        ".local/performance/source-before/src/unstd"
    )
    assert "--revision-baseline" in shlex.split(measurements["verify"]["run"])
    assert baseline_paths[0] != "bench/baselines/speeds.json"
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


def test_manual_gzip_diagnosis_skips_full_timing_and_retains_focused_evidence(
    workflow: dict[str, Any],
) -> None:
    option = workflow["on"]["workflow_dispatch"]["inputs"]["gzip_diagnostic"]
    assert option["type"] == "boolean"
    assert option["default"] is False
    for step in _measurement_steps(workflow).values():
        assert step["if"] == "${{ !inputs.gzip_diagnostic }}"
    focused = next(
        step
        for step in workflow["jobs"]["compare"]["steps"]
        if "-m bench.diagnostics.gzip_reads" in step.get("run", "")
    )
    assert focused["if"] == "${{ inputs.gzip_diagnostic }}"
    assert (
        focused["env"]["PYTHONPATH"]
        == (_measurement_steps(workflow)["verify"]["env"]["PYTHONPATH"])
    )
    tokens = shlex.split(focused["run"])
    assert tokens[tokens.index("--before-install") + 1].endswith("installed-before")
    assert tokens[tokens.index("--current-install") + 1].endswith("installed-current")
    assert tokens[tokens.index("--output") + 1].startswith(
        ".local/performance/evidence/"
    )
    assert "--baseline" not in tokens


def test_diagnostic_cannot_satisfy_the_full_function_latency_check(
    workflow: dict[str, Any],
) -> None:
    name = workflow["jobs"]["compare"]["name"]
    selection = re.fullmatch(
        r"\$\{\{ inputs\.gzip_diagnostic && '([^']+)' \|\| '([^']+)' \}\}", name
    )
    assert selection is not None, "the check context must follow diagnostic selection"
    diagnostic_context, full_context = selection.groups()
    assert full_context == "Function latency / Python 3.13"
    assert diagnostic_context != full_context
    assert diagnostic_context == "Gzip read diagnosis / Python 3.13"


def test_diagnostic_parent_entrypoint_needs_only_its_explicit_installation(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    focused = next(
        step
        for step in workflow["jobs"]["compare"]["steps"]
        if "-m bench.diagnostics.gzip_reads" in step.get("run", "")
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "bench").symlink_to(REPO_ROOT / "bench", target_is_directory=True)
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    # -S skips editable .pth hooks. Share real dependency files, excluding the
    # product itself, so neither the checkout nor a global install can rescue it.
    for directory in {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}:
        for path in Path(directory).iterdir():
            destination = dependencies / path.name
            if path.name != "unstd" and not destination.exists():
                destination.symlink_to(path, target_is_directory=path.is_dir())
    command = [
        sys.executable,
        "-S",
        "-m",
        "bench.diagnostics.gzip_reads",
        "run",
        "--help",
    ]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(workspace), str(dependencies))),
    }
    absent = subprocess.run(  # noqa: S603 — fixed interpreter/module and argv; no shell
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15
    )
    assert absent.returncode != 0
    assert "No module named 'unstd'" in absent.stderr
    installation = workspace / ".local/performance/installed-current"
    shutil.copytree(
        REPO_ROOT / "src/unstd",
        installation / "unstd",
        ignore=shutil.ignore_patterns("__pycache__", "*.so", "*.pyd"),
    )
    declared_path = focused["env"]["PYTHONPATH"].replace(
        "${{ github.workspace }}", str(workspace)
    )
    env["PYTHONPATH"] = os.pathsep.join((declared_path, str(dependencies)))
    present = subprocess.run(  # noqa: S603 — same clean entrypoint with its declared import path
        command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15
    )
    assert present.returncode == 0, present.stderr
    assert "--before-install" in present.stdout
    assert "--current-install" in present.stdout


@pytest.mark.parametrize(
    "adverse", [None, "missing", "duplicate", "symlink", "wrong-abi"]
)
def test_diagnostic_retains_exactly_one_native_build_per_revision(
    workflow: dict[str, Any], tmp_path: Path, adverse: str | None
) -> None:
    steps = workflow["jobs"]["compare"]["steps"]
    retain = next(step for step in steps if "evidence/native-" in step.get("run", ""))
    focused = next(
        step
        for step in steps
        if "-m bench.diagnostics.gzip_reads" in step.get("run", "")
    )
    builds = next(step for step in steps if "uv pip install" in step.get("run", ""))
    assert steps.index(builds) < steps.index(retain) < steps.index(focused)
    assert retain["if"] == focused["if"]
    assert not retain.get("continue-on-error", False)
    script = retain["run"].split("\n", 1)[1].rsplit("\nPYTHON", 1)[0]
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    name = f"_gzip_native{suffix}"
    payloads = {
        "before": b"before build bytes\x00",
        "current": b"current build bytes\xff",
    }
    for revision, payload in payloads.items():
        directory = tmp_path / f".local/performance/installed-{revision}/unstd/compress"
        directory.mkdir(parents=True)
        (directory / name).write_bytes(payload)
    current = tmp_path / ".local/performance/installed-current/unstd/compress"
    if adverse == "missing":
        (current / name).unlink()
    elif adverse == "duplicate":
        (current / "_gzip_native.foreign.so").write_bytes(b"ambiguous build")
    elif adverse == "symlink":
        (current / name).rename(current / "original")
        (current / name).symlink_to(current / "original")
    elif adverse == "wrong-abi":
        (current / name).rename(current / "_gzip_native.foreign.so")
    evidence = tmp_path / ".local/performance/evidence"
    evidence.mkdir()
    result = subprocess.run(  # noqa: S603 - execute the declared retention script with fixture files
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert (evidence / "native-before" / name).read_bytes() == payloads["before"]
    if adverse is not None:
        assert result.returncode != 0
        assert not (evidence / "native-current").exists()
    else:
        assert result.returncode == 0, result.stderr
        for revision, payload in payloads.items():
            assert list((evidence / f"native-{revision}").iterdir()) == [
                evidence / f"native-{revision}" / name
            ]
            assert (evidence / f"native-{revision}" / name).read_bytes() == payload
