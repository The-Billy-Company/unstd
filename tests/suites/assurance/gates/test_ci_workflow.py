"""GitHub's grouped CI retains every environment and rejects incomplete results."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING
import zipfile

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend
from tests.tooling.ci import groups as ci


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


def _group_runs(job):
    return [
        step["run"]
        for step in job["steps"]
        if "tests.tooling.ci run " in step.get("run", "")
    ]


def test_public_contract_groups_run_on_every_other_supported_os(workflow):
    job = workflow["jobs"]["platforms"]
    matrix = job["strategy"]["matrix"]
    assert set(matrix) == {"os", "group"}
    assert set(matrix["os"]) == {"macos-latest", "windows-latest"}
    assert {group["id"] for group in matrix["group"]} == {"api", "contracts"}
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job["strategy"]["fail-fast"] is False
    sync = shlex.split(_sync(job))
    assert {"--locked", "--all-extras"} <= set(sync)
    assert _group_runs(job) == [
        "uv run --no-sync python -m tests.tooling.ci run ${{ matrix.group.id }}"
        " -- --require-extras -q"
    ]


def test_every_declared_extra_runs_alone_and_requires_exactly_what_it_installs(
    workflow,
):
    job = workflow["jobs"]["extras"]
    rows = job["strategy"]["matrix"]["install"]
    assert len(rows) == len({row["id"] for row in rows})
    assert job["strategy"]["fail-fast"] is False
    installs = []
    for row in rows:
        sync, pytest_args = shlex.split(row["sync"]), shlex.split(row["pytest"])
        installed = set(sync[1::2])
        assert sync[::2] == ["--extra"] * len(installed)
        assert pytest_args[::2] == ["--require-extra"] * len(installed)
        # A lane admitting fewer extras than it installs could silently skip one.
        assert set(pytest_args[1::2]) == installed
        installs.append(frozenset(installed))
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        extras = tomllib.load(stream)["project"]["optional-dependencies"]
    declared = {name for name, requirements in extras.items() if requirements}
    assert {frozenset({name}) for name in declared} <= set(installs)
    assert set().union(*installs) == declared
    template = _sync(job)
    assert "--all-extras" not in template
    assert "${{ matrix.install.sync }}" in template
    assert _group_runs(job) == [
        f"uv run --no-sync python -m tests.tooling.ci run {group}"
        " -- ${{ matrix.install.pytest }} -q"
        for group in ("api", "contracts")
    ]
    second = next(s for s in job["steps"] if "run contracts" in s.get("run", ""))
    assert second["if"] == "${{ !cancelled() }}"


def test_cached_dependency_setup_rebuilds_source_before_running_tests(workflow):
    for identity in ("lint", "test", "platforms", "extras", "resources", "native"):
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


def test_floor_install_restores_canonical_lock_without_replacing_active_versions(
    workflow, tmp_path: Path
):
    job = workflow["jobs"]["test"]
    steps = job["steps"]
    sync = next(
        index for index, step in enumerate(steps) if step.get("run") == _sync(job)
    )
    restore = steps[sync + 1]
    assert restore["if"] == "matrix.environment.id == 'floors'"
    command = shlex.split(restore["run"])
    assert command == ["git", "restore", "--source=HEAD", "--", "uv.lock"]
    assert not restore.get("continue-on-error", False)
    assert sync + 1 < next(
        index
        for index, step in enumerate(steps)
        if step.get("run", "").startswith("uv build ")
    )
    assert sync + 1 < next(
        index
        for index, step in enumerate(steps)
        if "tests.tooling.ci run " in step.get("run", "")
    )
    prime = next(
        step
        for step in steps
        if step.get("name")
        == "Prime canonical artifacts without replacing dependency floors"
    )
    assert prime["if"] == (
        "matrix.group.id == 'packaging' && matrix.environment.id == 'floors'"
    )
    assert prime["env"]["UV_PROJECT_ENVIRONMENT"].startswith("${{ runner.temp }}/")
    assert {
        "--locked",
        "--all-extras",
        "--no-default-groups",
        "--no-install-project",
    } <= set(shlex.split(prime["run"]))

    git, uv = shutil.which("git"), shutil.which("uv")
    assert git is not None, "floor workflow probe requires git"
    assert uv is not None, "floor workflow probe requires uv"
    source = tmp_path / "source"
    source.mkdir()
    wheels = source / "wheels"
    wheels.mkdir()
    for version in ("1.0", "2.0"):
        info = f"ci_floor_probe-{version}.dist-info"
        files = {
            "ci_floor_probe.py": f"VERSION = {version!r}\n",
            f"{info}/METADATA": (
                f"Metadata-Version: 2.3\nName: ci-floor-probe\nVersion: {version}\n\n"
            ),
            f"{info}/WHEEL": (
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
            ),
        }
        files[f"{info}/RECORD"] = "".join(
            f"{name},,\n" for name in (*files, f"{info}/RECORD")
        )
        with zipfile.ZipFile(
            wheels / f"ci_floor_probe-{version}-py3-none-any.whl", "w"
        ) as wheel:
            for name, data in files.items():
                wheel.writestr(name, data)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "floor-workflow-probe"\nversion = "0"\n'
        'requires-python = ">=3.12"\ndependencies = ["ci-floor-probe>=1.0"]\n'
        "[tool.uv]\npackage = false\n"
    )
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("UV_")
        and name not in {"VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH"}
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        UV_PYTHON_DOWNLOADS="never",
    )

    def execute(*arguments):
        return subprocess.run(  # noqa: S603 — fixed tools and synthetic offline inputs
            arguments,
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

    options = (
        "--offline",
        "--no-index",
        "--find-links",
        str(wheels),
        "--python",
        sys.executable,
    )
    execute(uv, "lock", *options)
    lock = source / "uv.lock"
    canonical = lock.read_bytes()
    assert (
        next(
            p["version"]
            for p in tomllib.loads(canonical.decode())["package"]
            if p["name"] == "ci-floor-probe"
        )
        == "2.0"
    )
    execute(git, "init", "--quiet")
    execute(git, "add", "uv.lock")
    execute(
        git,
        "-c",
        "user.name=CI probe",
        "-c",
        "user.email=ci@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--quiet",
        "-m",
        "canonical lock",
    )
    execute(uv, "sync", *options, "--resolution", "lowest-direct")
    assert lock.read_bytes() != canonical
    assert (
        next(
            p["version"]
            for p in tomllib.loads(lock.read_text())["package"]
            if p["name"] == "ci-floor-probe"
        )
        == "1.0"
    )
    active = source / ".venv"
    python = active / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    probe = (
        str(python),
        "-I",
        "-c",
        "from ci_floor_probe import VERSION; print(VERSION)",
    )
    assert execute(*probe).stdout.strip() == "1.0"
    installed = {
        p.relative_to(active): p.read_bytes()
        for p in active.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    execute(*command)
    assert lock.read_bytes() == canonical
    # Cache canonical consumers in another environment while retaining floors.
    cache_environment = tmp_path / "canonical-cache"
    environment["UV_PROJECT_ENVIRONMENT"] = str(cache_environment)
    try:
        execute(
            uv,
            "sync",
            *options,
            "--locked",
            "--all-extras",
            "--no-default-groups",
            "--no-install-project",
        )
    finally:
        environment.pop("UV_PROJECT_ENVIRONMENT")
    cache_python = cache_environment / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    assert execute(str(cache_python), *probe[1:]).stdout.strip() == "2.0"
    assert lock.read_bytes() == canonical
    assert {
        p.relative_to(active): p.read_bytes()
        for p in active.rglob("*")
        if p.is_file() and not p.is_symlink()
    } == installed
    assert execute(*probe).stdout.strip() == "1.0"


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
