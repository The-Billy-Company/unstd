"""Dependency evidence is required before source can reach a privileged publisher."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tomllib
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
        for name in ("supply-chain", "ci", "release")
    }


def _steps(workflow):
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _uses(steps, action):
    return [step for step in steps if step.get("uses", "").startswith(action + "@")]


def _needs(job):
    value = job.get("needs", [])
    return {value} if isinstance(value, str) else set(value)


def _audit_step(workflows):
    steps = [
        step
        for step in _steps(workflows["supply-chain"])
        if re.search(r"\buv audit\b", step.get("run", ""))
    ]
    assert len(steps) == 1, "one universal audit covers the complete locked graph"
    return steps[0]


def _wheel_setup_commands(workflows):
    build = next(
        step
        for step in workflows["release"]["jobs"]["wheels"]["steps"]
        if "cibuildwheel " in step.get("run", "")
    )
    settings = build["env"]
    # cibuildwheel's automatic installers would otherwise resolve the declared
    # floors again before running code beside our publishable wheel.
    assert settings["CIBW_TEST_EXTRAS"] == ""
    assert settings["CIBW_TEST_REQUIRES"] == ""
    tokens = shlex.split(settings["CIBW_BEFORE_TEST"])
    commands = [[]]
    for argument in tokens:
        if argument == "&&":
            commands.append([])
        else:
            commands[-1].append(argument)
    assert len(commands) == 4
    return settings["CIBW_BEFORE_TEST"], commands


def _linux_frontend_commands(workflows):
    build = next(
        step
        for step in workflows["release"]["jobs"]["wheels"]["steps"]
        if "cibuildwheel " in step.get("run", "")
    )
    return [
        shlex.split(command)
        for command in build["env"]["CIBW_BEFORE_ALL_LINUX"].split(" && ")
    ]


def _wheel_test_commands(workflows):
    command, setup = _wheel_setup_commands(workflows)
    return command, *setup[2:]


def _repair_tool_export(workflows):
    steps = workflows["release"]["jobs"]["wheels"]["steps"]
    build = next(step for step in steps if "cibuildwheel " in step.get("run", ""))
    constraints = build["env"]["CIBW_DEPENDENCY_VERSIONS"]
    (export,) = [step for step in steps if step.get("run", "").startswith("uv export ")]
    tokens = shlex.split(export["run"])
    assert tokens[tokens.index("--output-file") + 1] == constraints
    assert constraints == "${{ runner.temp }}/release-constraints.txt"
    assert steps.index(export) < steps.index(build)
    return tokens


@pytest.mark.parametrize("job", ["test", "native"])
def test_ci_checks_the_actual_interpreter_before_crypto_tests(workflows, job):
    steps = workflows["ci"]["jobs"][job]["steps"]
    (preflight,) = [
        step for step in steps if "-m tests.tooling.runtime" in step.get("run", "")
    ]
    assert preflight["run"] == "uv run --no-sync python -m tests.tooling.runtime --json"
    assert "if" not in preflight
    assert not preflight.get("continue-on-error", False)
    tests = next(
        step
        for step in steps
        if "python -m pytest" in step.get("run", "")
        or "tests.tooling.ci run" in step.get("run", "")
    )
    assert steps.index(preflight) < steps.index(tests)


def test_release_and_daily_audit_check_their_actual_interpreter(workflows):
    for workflow, job in (("release", "wheels"), ("supply-chain", "audit")):
        steps = workflows[workflow]["jobs"][job]["steps"]
        (preflight,) = [
            step for step in steps if "-m tests.tooling.runtime" in step.get("run", "")
        ]
        assert (
            preflight["run"]
            == "uv run --no-sync python -m tests.tooling.runtime --json"
        )
        assert "if" not in preflight
        assert not preflight.get("continue-on-error", False)


def test_wheel_tests_check_the_installed_interpreters_stdlib_tls_engine():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    command = project["tool"]["cibuildwheel"]["test-command"][0]
    assert shlex.split(command) == [
        "python",
        "{project}/tests/tooling/runtime/tls.py",
        "--json",
    ]


def test_reusable_daily_gate_is_bounded_read_only_and_uses_pinned_actions(workflows):
    workflow = workflows["supply-chain"]
    assert {"workflow_call", "workflow_dispatch", "schedule"} <= workflow["on"].keys()
    assert workflow["on"]["schedule"][0]["cron"]
    assert workflow["permissions"] == {"contents": "read"}
    assert len(workflow["jobs"]) == 1
    for job in workflow["jobs"].values():
        assert "matrix" not in job.get("strategy", {})
        assert 1 <= job["timeout-minutes"] <= 20
        assert not job.get("continue-on-error", False)
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
        for step in job["steps"]:
            assert not step.get("continue-on-error", False)
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
    (checkout,) = _uses(_steps(workflow), "actions/checkout")
    assert checkout["with"]["persist-credentials"] is False


def test_cached_setup_locks_its_executable_and_keys_dependency_inputs(workflows):
    (setup,) = _uses(_steps(workflows["supply-chain"]), "astral-sh/setup-uv")
    settings = setup["with"]
    assert settings["enable-cache"] in (True, "auto")
    assert {"uv.lock", "pyproject.toml"} <= set(
        settings["cache-dependency-glob"].splitlines()
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", settings["version"])


def test_audit_keeps_universal_marker_coverage_and_all_extras_and_groups(workflows):
    step = _audit_step(workflows)
    assert "if" not in step
    tokens = shlex.split(step["run"])
    audit = tokens[tokens.index("audit") + 1 :]
    assert "--locked" in audit
    assert audit[audit.index("--output-format") + 1] == "json"
    excluded = {
        "--frozen",
        "--no-extra",
        "--no-dev",
        "--no-group",
        "--no-default-groups",
        "--only-group",
        "--only-dev",
        "--ignore",
        "--ignore-until-fixed",
        "--python-version",
        "--python-platform",
        "--allow-insecure-host",
    }
    assert not {token.split("=", 1)[0] for token in audit} & excluded


@pytest.mark.parametrize("exit_code", [1, 2])
def test_advisory_finding_or_service_failure_cannot_be_swallowed(
    workflows, tmp_path: Path, exit_code: int
):
    # Run the actual shell step with a controlled scanner. Exit 1 is a finding;
    # exit 2 is infrastructure failure. Both must keep the workflow red.
    command = _audit_step(workflows)["run"]
    executable = tmp_path / "uv"
    called = tmp_path / "scanner-called"
    executable.write_text(
        "#!/bin/sh\nprintf '{}\\n'\n"
        'printf called > "$SUPPLY_AUDIT_CALLED"\n'
        f"exit {exit_code}\n"
    )
    executable.chmod(0o700)
    (tmp_path / ".local/supply-chain").mkdir(parents=True)
    result = subprocess.run(  # noqa: S603 — repository-owned workflow under test
        ["/bin/bash", "-e", "-o", "pipefail", "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SUPPLY_AUDIT_CALLED": str(called),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert called.read_text() == "called"
    assert result.returncode != 0, result.stdout + result.stderr


def test_dependency_evidence_is_retained_even_when_the_gate_fails(workflows):
    (upload,) = _uses(_steps(workflows["supply-chain"]), "actions/upload-artifact")
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["name"].startswith("supply-chain-evidence")
    assert settings["include-hidden-files"] is True
    assert settings["if-no-files-found"] == "error"
    assert 1 <= settings["retention-days"] <= 90


def test_audit_json_is_validated_before_installing_or_accepting_the_gate(workflows):
    # uv currently exits zero for quarantined/deprecated projects, so its JSON
    # report must pass our policy after the network scan succeeds.
    steps = _steps(workflows["supply-chain"])
    audit = _audit_step(workflows)
    tokens = shlex.split(audit["run"])
    report = tokens[tokens.index(">") + 1]
    (policy,) = [
        step for step in steps if "supply_chain audit-report" in step.get("run", "")
    ]
    assert shlex.split(policy["run"]) == [
        "python",
        "-m",
        "tests.tooling.supply_chain",
        "audit-report",
        report,
    ]
    sync = next(step for step in steps if step.get("run", "").startswith("uv sync "))
    assert steps.index(audit) < steps.index(policy) < steps.index(sync)
    assert "if" not in policy
    assert not policy.get("continue-on-error", False)


def test_ci_requires_the_reusable_supply_chain_gate(workflows):
    jobs = workflows["ci"]["jobs"]
    gate = jobs["supply-chain"]
    assert gate["uses"] == "./.github/workflows/supply-chain.yml"
    assert not gate.get("continue-on-error", False)
    assert "if" not in gate
    assert "supply-chain" in _needs(jobs["result"])


def test_dedicated_gate_requires_parser_and_builder_before_any_optional_skip(
    workflows,
):
    steps = _steps(workflows["supply-chain"])
    sync = next(step for step in steps if step.get("run", "").startswith("uv sync "))
    tokens = shlex.split(sync["run"])
    assert {"--locked", "--no-install-project"} <= set(tokens)
    assert {
        tokens[index + 1]
        for index, value in enumerate(tokens)
        if value == "--only-group"
    } == {"supply-chain", "release"}
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert {
        re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0]
        for requirement in manifest["dependency-groups"]["supply-chain"]
    } == {"pytest", "hypothesis", "packaging", "ruamel-yaml"}
    parser = next(step for step in steps if "import ruamel.yaml" in step.get("run", ""))
    assert "cibuildwheel" in parser["run"]
    tests = next(step for step in steps if "-m pytest" in step.get("run", ""))
    assert steps.index(sync) < steps.index(parser) <= steps.index(tests)
    assert not parser.get("continue-on-error", False)
    assert "if" not in parser


def test_only_verified_release_artifacts_reach_the_isolated_oidc_publisher(workflows):
    workflow = workflows["release"]
    jobs = workflow["jobs"]
    publisher = jobs["pypi"]
    assert publisher["permissions"] == {"id-token": "write"}
    assert publisher["environment"] == "pypi"
    assert "refs/tags/v" in publisher["if"]
    assert "verify" in _needs(publisher)
    steps = publisher["steps"]
    assert not _uses(steps, "actions/checkout")
    assert not _uses(steps, "astral-sh/setup-uv")
    downloads = _uses(steps, "actions/download-artifact")
    assert {step["with"]["name"] for step in downloads} == {
        "release-verified",
        "release-evidence",
    }
    download = next(
        step for step in downloads if step["with"]["name"] == "release-verified"
    )
    assert download["with"]["name"] == "release-verified"
    assert download["with"]["path"] == "dist"
    assert all("pattern" not in step["with"] for step in downloads)
    (integrity,) = [step for step in steps if "run" in step]
    tokens = shlex.split(integrity["run"])
    assert tokens[:4] == ["python", "-I", "evidence/release.py", "verify"]
    assert tokens[tokens.index("--dist") + 1] == "dist"
    assert tokens[tokens.index("--expected-manifest") + 1] == "evidence/manifest.json"
    assert "--tag" in tokens
    assert not integrity.get("continue-on-error", False)
    (publish,) = _uses(steps, "pypa/gh-action-pypi-publish")
    assert publish["with"]["attestations"] is True
    assert all(steps.index(step) < steps.index(integrity) for step in downloads)
    assert steps.index(integrity) < steps.index(publish)


def test_release_verification_uses_only_named_build_outputs_and_separate_evidence(
    workflows,
):
    job = workflows["release"]["jobs"]["verify"]
    assert {"wheels", "sdist"} <= _needs(job)
    assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}
    steps = job["steps"]
    downloads = _uses(steps, "actions/download-artifact")
    assert downloads
    for download in downloads:
        settings = download["with"]
        selected = settings.get("name", settings.get("pattern", ""))
        assert selected.startswith("release-raw-")
        assert "evidence" not in selected
    uploads = _uses(steps, "actions/upload-artifact")
    verified = next(
        step for step in uploads if step["with"]["name"] == "release-verified"
    )
    evidence = next(
        step for step in uploads if step["with"]["name"].startswith("release-evidence")
    )
    assert verified["with"]["path"] == "dist/*"
    assert evidence["with"]["path"] != verified["with"]["path"]
    assert evidence["if"] == "always()"
    assert "if" not in verified
    (verification,) = [
        step for step in steps if "tests.tooling.release verify" in step.get("run", "")
    ]
    assert "--dist dist" in verification["run"]
    assert "--manifest evidence/manifest.json" in verification["run"]
    assert steps.index(verification) < steps.index(verified)
    assert steps.index(verification) < steps.index(evidence)
    assert not job.get("continue-on-error", False)
    assert all(not step.get("continue-on-error", False) for step in steps)


def test_release_wheel_shards_keep_every_platform_and_supported_interpreter(workflows):
    wheels = workflows["release"]["jobs"]["wheels"]
    matrix = wheels["strategy"]["matrix"]
    assert set(matrix) == {"os", "python"}
    assert set(matrix["os"]) == {
        "ubuntu-latest",
        "ubuntu-24.04-arm",
        "macos-15-intel",
        "macos-14",
        "windows-latest",
        "windows-11-arm",
    }
    assert matrix["python"] == ["312", "313", "314"]
    assert wheels["strategy"]["fail-fast"] is False
    build = next(s for s in wheels["steps"] if "cibuildwheel " in s.get("run", ""))
    assert build["env"]["CIBW_BUILD"] == "cp${{ matrix.python }}-*"
    (upload,) = _uses(wheels["steps"], "actions/upload-artifact")
    assert upload["with"]["name"] == (
        "release-raw-wheels-${{ matrix.os }}-${{ matrix.python }}"
    )


def test_wheels_use_the_published_sdist_and_authenticated_locked_build_tools(workflows):
    jobs = workflows["release"]["jobs"]
    source = jobs["sdist"]
    wheels = jobs["wheels"]
    assert not _needs(source)
    assert _needs(wheels) == {"sdist"}
    assert {"gate", "ci-status", "verify"} <= _needs(jobs["pypi"])
    (source_upload,) = _uses(source["steps"], "actions/upload-artifact")
    (source_download,) = _uses(wheels["steps"], "actions/download-artifact")
    assert source_upload["with"]["name"] == "release-raw-source"
    assert source_download["with"]["name"] == source_upload["with"]["name"]
    assert source_download["with"]["path"] == "source"
    sync = next(
        step for step in wheels["steps"] if step.get("run", "").startswith("uv sync ")
    )
    assert {"--locked", "--no-install-project"} <= set(shlex.split(sync["run"]))
    assert "--only-group release" in sync["run"]
    build = next(
        step for step in wheels["steps"] if "cibuildwheel " in step.get("run", "")
    )
    assert shlex.split(build["run"]) == [
        "uv",
        "run",
        "--no-sync",
        "cibuildwheel",
        "source/*.tar.gz",
        "--output-dir",
        "dist",
    ]
    assert wheels["steps"].index(source_download) < wheels["steps"].index(build)
    assert wheels["steps"].index(sync) < wheels["steps"].index(build)
    before = shlex.split(build["env"]["CIBW_BEFORE_BUILD"])
    assert before[:2] == ["uv", "export"]
    assert {"--locked", "--no-emit-project"} <= set(before)
    assert before[before.index("--project") + 1] == "{package}"
    assert before[before.index("--only-group") + 1] == "build"
    constraints = before[before.index("--output-file") + 1]
    frontend, separator, options = build["env"]["CIBW_BUILD_FRONTEND"].partition(
        "; args:"
    )
    assert frontend == "uv"
    assert separator
    arguments = shlex.split(options)
    assert arguments[arguments.index("--build-constraints") + 1] == constraints
    assert "--require-hashes" in arguments
    for step in _uses([*source["steps"], *wheels["steps"]], "astral-sh/setup-uv"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", step["with"]["version"])


def test_sdist_builder_authenticates_the_exported_build_closure(workflows):
    steps = workflows["release"]["jobs"]["sdist"]["steps"]
    export = next(
        step for step in steps if step.get("run", "").startswith("uv export ")
    )
    before = shlex.split(export["run"])
    assert {"--locked", "--no-emit-project"} <= set(before)
    assert before[before.index("--only-group") + 1] == "build"
    constraints = before[before.index("--output-file") + 1]
    build = next(step for step in steps if step.get("run", "").startswith("uv build "))
    arguments = shlex.split(build["run"])
    assert "--sdist" in arguments
    assert arguments[arguments.index("--build-constraints") + 1] == constraints
    assert "--require-hashes" in arguments
    assert steps.index(export) < steps.index(build)


def test_wheel_test_dependencies_use_only_the_authenticated_locked_closure(workflows):
    _, export, install = _wheel_test_commands(workflows)
    assert export[:2] == ["uv", "export"]
    assert {"--locked", "--no-emit-project", "--no-default-groups"} <= set(export)
    assert "--no-hashes" not in export
    assert export[export.index("--project") + 1] == "{package}"
    assert {
        export[index + 1] for index, option in enumerate(export) if option == "--extra"
    } == {"serde", "crypto", "compress"}
    assert export[export.index("--group") + 1] == "supply-chain"
    assert install[:3] == ["uv", "pip", "install"]
    assert {"--require-hashes", "--require-build-hashes", "--no-deps"} <= set(install)
    assert (
        install[install.index("--requirements") + 1]
        == export[export.index("--output-file") + 1]
    )


def test_linux_container_frontend_uses_the_authenticated_locked_tool(workflows):
    export, install = _linux_frontend_commands(workflows)
    assert export[:2] == ["uv", "export"]
    assert {"--locked", "--no-emit-project"} <= set(export)
    assert "--no-hashes" not in export
    assert export[export.index("--project") + 1] == "{package}"
    assert export[export.index("--only-group") + 1] == "release-uv"
    assert install[:4] == ["python", "-m", "pip", "install"]
    assert {"--no-deps", "--require-hashes"} <= set(install)
    assert install[install.index("--only-binary") + 1] == ":all:"
    assert install[install.index("--prefix") + 1] == "/usr/local"
    assert (
        install[install.index("--requirement") + 1]
        == export[export.index("--output-file") + 1]
    )
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        dependencies = tomllib.load(stream)["dependency-groups"]["release-uv"]
    for setup in _uses(
        workflows["release"]["jobs"]["wheels"]["steps"], "astral-sh/setup-uv"
    ):
        assert dependencies == [f"uv=={setup['with']['version']}"]


def test_windows_arm_source_backend_uses_the_authenticated_build_closure(workflows):
    _, (export, tools, _, install) = _wheel_setup_commands(workflows)
    assert export[:2] == ["uv", "export"]
    assert {"--locked", "--no-emit-project"} <= set(export)
    assert export[export.index("--only-group") + 1] == "backend-build"
    constraints = export[export.index("--output-file") + 1]
    assert tools[:3] == ["uv", "pip", "install"]
    assert {"--no-deps", "--require-hashes"} <= set(tools)
    assert tools[tools.index("--only-binary") + 1] == ":all:"
    assert tools[tools.index("--requirements") + 1] == constraints
    assert install[install.index("--build-constraints") + 1] == constraints
    assert "--require-build-hashes" in install
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        build = tomllib.load(stream)["dependency-groups"]["backend-build"]
    for package in ("setuptools-scm", "wheel", "cmake", "maturin"):
        assert any(
            requirement.startswith(package)
            and "sys_platform == 'win32'" in requirement
            and "platform_machine == 'ARM64'" in requirement
            for requirement in build
        )


def test_platform_repair_tools_are_constrained_to_the_audited_release_group(workflows):
    export = _repair_tool_export(workflows)
    assert {"--locked", "--no-emit-project"} <= set(export)
    assert "--no-hashes" not in export
    assert export[export.index("--only-group") + 1] == "release"


@pytest.mark.parametrize(
    ("failed_stage", "status"),
    [(0, 0), *((stage, status) for stage in range(1, 5) for status in (1, 2))],
)
def test_wheel_test_setup_stops_when_export_or_authenticated_install_fails(
    workflows, tmp_path: Path, failed_stage: int, status: int
):
    command, _, _ = _wheel_test_commands(workflows)
    executable = tmp_path / "uv"
    calls = tmp_path / "uv-calls"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SUPPLY_TEST_CALLS"\n'
        'count=$(wc -l < "$SUPPLY_TEST_CALLS")\n'
        'if [ "$count" -eq "$SUPPLY_FAILED_STAGE" ]; then\n'
        '  exit "$SUPPLY_STAGE_STATUS"\n'
        "fi\nexit 0\n"
    )
    executable.chmod(0o700)
    process = subprocess.run(  # noqa: S603 — repository-owned workflow under test
        ["/bin/bash", "-e", "-o", "pipefail", "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SUPPLY_TEST_CALLS": str(calls),
            "SUPPLY_FAILED_STAGE": str(failed_stage),
            "SUPPLY_STAGE_STATUS": str(status),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert process.returncode == status
    observed = [shlex.split(line) for line in calls.read_text().splitlines()]
    assert observed[0][0] == "export"
    assert len(observed) == (failed_stage or 4)
    if len(observed) > 1:
        assert observed[1][:2] == ["pip", "install"]


@pytest.mark.parametrize("purpose", ["tests", "repair", "build", "frontend"])
def test_real_offline_wheel_export_keeps_every_transitive_locked_hash(
    workflows, tmp_path: Path, purpose: str
):
    if purpose == "tests":
        _, export, _ = _wheel_test_commands(workflows)
    elif purpose == "repair":
        export = _repair_tool_export(workflows)
    elif purpose == "frontend":
        export, _ = _linux_frontend_commands(workflows)
    else:
        _, (export, _, _, _) = _wheel_setup_commands(workflows)
    # Freeze both inputs so concurrent development cannot change our closure
    # oracle while uv checks and exports the same snapshot.
    snapshot = tmp_path / "source"
    snapshot.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        (snapshot / name).write_bytes((REPO_ROOT / name).read_bytes())
    lock_before = (snapshot / "uv.lock").read_bytes()
    lock = tomllib.loads(lock_before.decode())
    manifest = tomllib.loads((snapshot / "pyproject.toml").read_text())
    executable = shutil.which("uv")
    assert executable is not None, "wheel test export requires uv on PATH"
    export[0] = executable
    if "--project" in export:
        export[export.index("--project") + 1] = str(snapshot)
    else:
        export.extend(("--project", str(snapshot)))
    output = tmp_path / "test-requirements.txt"
    export[export.index("--output-file") + 1] = str(output)
    process = subprocess.run(  # noqa: S603 — explicit offline lockfile export
        export,
        cwd=tmp_path,
        env={**os.environ, "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert (snapshot / "uv.lock").read_bytes() == lock_before
    root = next(row for row in lock["package"] if row["source"] == {"editable": "."})
    by_name = {}
    for row in lock["package"]:
        by_name.setdefault(row["name"], []).append(row)
    seeds = list(root.get("dependencies", []))
    if purpose == "tests":
        for extra in manifest["tool"]["cibuildwheel"]["test-extras"]:
            seeds.extend(root["optional-dependencies"][extra])
        seeds.extend(root["dev-dependencies"]["supply-chain"])
    elif purpose == "repair":
        seeds.extend(root["dev-dependencies"]["release"])
    elif purpose == "frontend":
        seeds.extend(root["dev-dependencies"]["release-uv"])
    else:
        seeds.extend(root["dev-dependencies"]["backend-build"])
    pending = [edge["name"] for edge in seeds]
    closure = set()
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        closure.add(name)
        for row in by_name[name]:
            pending.extend(edge["name"] for edge in row.get("dependencies", []))
    expected = {
        (row["name"], row["version"]): {
            artifact["hash"].removeprefix("sha256:")
            for artifact in [
                *row.get("wheels", []),
                *([row["sdist"]] if "sdist" in row else []),
            ]
        }
        for name in closure
        for row in by_name[name]
    }
    observed = {}
    for line in output.read_text().replace("\\\n", "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9][\w.-]*)==([^\s;]+)", line)
        assert match is not None, f"requirement must be exactly pinned: {line}"
        identity = match[1], match[2]
        assert identity in expected
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", line))
        assert hashes
        assert hashes <= expected[identity]
        observed.setdefault(identity, set()).update(hashes)
    assert observed.keys() == expected.keys()
    # Keep every explicitly declared wheel-test tool when automatic floor
    # installation is disabled, including transitive harness dependencies.
    if purpose == "tests":
        assert {
            re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0]
            for requirement in manifest["tool"]["cibuildwheel"]["test-requires"]
        } <= {name for name, _ in observed}
    elif purpose == "repair":
        assert ("delocate", "0.13.0") in observed
        assert (
            "delocate==0.13.0 ; sys_platform == 'darwin'"
            in manifest["dependency-groups"]["release"]
        )
