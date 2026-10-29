"""GitHub's grouped CI retains every environment and rejects incomplete results."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python
from tests.tooling.ci import groups as ci
from tests.tooling.ci.sharding import parse_shard
from tests.tooling.release import verify, wheel_tests


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


def _assert_complete_partitions(rows):
    partitions = {}
    assert len({row["report"] for row in rows}) == len(rows)
    for row in rows:
        flags = shlex.split(row["shard"])
        if flags:
            assert len(flags) == 2
            assert flags[0] == "--shard"
            index, count = parse_shard(flags[1])
            assert row["report"] == f"{row['id']}-{index}-of-{count}"
        else:
            index, count = 1, 1
            assert row["report"] == row["id"]
        partitions.setdefault(row["id"], []).append((index, count))
    for members in partitions.values():
        counts = {count for _, count in members}
        assert len(counts) == 1
        (count,) = counts
        assert sorted(members) == [(index, count) for index in range(1, count + 1)]
    return partitions


def test_every_owned_group_runs_in_every_supported_dependency_environment(workflow):
    job = workflow["jobs"]["test"]
    matrix = job["strategy"]["matrix"]
    # An include-only row or exclusion could quietly leave a group untested.
    assert set(matrix) == {"group", "environment"}
    groups = matrix["group"]
    partitions = _assert_complete_partitions(groups)
    assert set(partitions) == {group.id for group in ci.GROUPS}
    assert all(group["name"] for group in groups)
    assert "${{ matrix.group.name }}" in job["name"]
    assert "${{ matrix.environment.name }}" in job["name"]
    assert job["strategy"]["fail-fast"] is False
    environments = matrix["environment"]
    assert len(environments) == len({item["id"] for item in environments}) == 6
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
        ("3.15", True, True, None, True),
        ("3.15", False, True, "lowest-direct", True),
        ("3.15", True, False, None, False),
    }


def test_tooling_owner_installs_required_builder_and_compiler_tools_in_every_environment(
    workflow,
):
    owner = next(group.id for group in ci.GROUPS if "gates" in group.suites)
    job = workflow["jobs"]["test"]
    for group in job["strategy"]["matrix"]["group"]:
        tools = (
            ["--group", "release", "--group", "build"] if group["id"] == owner else []
        )
        assert shlex.split(group["dependencies"]) == tools
        for environment in job["strategy"]["matrix"]["environment"]:
            assert shlex.split(
                _render(_sync(job), group=group, environment=environment)
            ) == [
                "uv",
                "sync",
                *shlex.split(environment["sync"]),
                "--group",
                "dev",
                *tools,
                "--reinstall-package",
                "unstd",
                "--python",
                environment["python"],
            ]


def test_matrix_python_uses_the_managed_runtime_instead_of_the_runner(workflow):
    assert workflow["env"]["UV_MANAGED_PYTHON"] == "1"
    for job in workflow["jobs"].values():
        assert job.get("env", {}).get("UV_MANAGED_PYTHON", "1") == "1"


def test_default_workflow_interpreters_match_the_deployed_python():
    version = (REPO_ROOT / ".python-version").read_text().strip()
    assert re.fullmatch(r"3\.15\.\d+", version)
    runtime = ".".join(version.split(".")[:2])
    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    for source in (REPO_ROOT / ".github/workflows").glob("*.yml"):
        for job in yaml.load(source.read_text())["jobs"].values():
            for step in job.get("steps", []):
                selected = step.get("with", {}).get("python-version", "")
                if re.fullmatch(r"3\.\d+", selected):
                    assert selected == runtime, (source.name, step["name"])
                for requested in re.findall(
                    r"--python (3\.\d+)(?:\s|$)", step.get("run", "")
                ):
                    assert requested == runtime, (source.name, step["name"])


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
            assert tokens == [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "tests.tooling.ci",
                "run",
                *shlex.split(group["shard"]),
                group["id"],
                "--",
                *shlex.split(environment["pytest"]),
                "-q",
            ]
            assert ("--require-extras" in tokens) == (
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
    assert set(_assert_complete_partitions(matrix["group"])) == {"api", "contracts"}
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job["strategy"]["fail-fast"] is False
    sync = shlex.split(_sync(job))
    assert {"--locked", "--all-extras"} <= set(sync)
    assert _group_runs(job) == [
        "uv run --no-sync python -m tests.tooling.ci run ${{ matrix.group.shard }} ${{ matrix.group.id }}"
        " -- --require-extras -q"
    ]


def test_sanitizer_shards_retain_both_engines_every_module_and_failure_evidence(
    workflow,
):
    job = workflow["jobs"]["native-sanitizers"]
    matrix = job["strategy"]["matrix"]
    assert set(matrix) == {"python", "rust-text", "shard"}
    assert matrix["python"] == ["3.14", "3.15"]
    assert matrix["rust-text"] == ["0", "1"]
    assert matrix["shard"] == [1, 2, 3]
    assert job["strategy"]["fail-fast"] is False
    step = next(step for step in job["steps"] if step.get("id") == "tests")
    command = next(line for line in step["run"].splitlines() if "-m pytest " in line)
    paths = {
        "tests/suites/product/clone",
        "tests/suites/product/text",
        "tests/suites/product/serde",
        "tests/suites/product/crypto",
        "tests/suites/product/compress",
        "tests/suites/product/containers",
        "tests/suites/product/fs",
        "tests/suites/product/pack",
        "tests/suites/product/math",
        "tests/suites/product/rand",
        "tests/suites/product/dataset",
    }
    for shard in matrix["shard"]:
        tokens = shlex.split(command.replace("${{ matrix.shard }}", str(shard)))
        assert {token for token in tokens if token.startswith("tests/suites/")} == paths
        assert tokens[tokens.index("--ci-shard") + 1] == f"{shard}/3"
        assert tokens[tokens.index("-p") + 1] == "tests.tooling.ci.sharding"
        assert f"--junitxml=.local/native-sanitizers-{shard}.xml" in tokens
    summary = next(step for step in job["steps"] if "Summarize" in step.get("name", ""))
    artifact = next(
        step for step in job["steps"] if "upload-artifact@" in step.get("uses", "")
    )
    assert ".local/native-sanitizers-${{ matrix.shard }}.xml" in summary["run"]
    assert artifact["with"]["path"].splitlines() == [
        ".local/native-sanitizers-${{ matrix.shard }}.xml",
        ".local/proc-memory/manifest.json",
        ".local/proc-memory/tests.txt",
        ".local/proc-memory/tests.xml",
    ]
    assert "${{ matrix.shard }}" in artifact["with"]["name"]
    assert "${{ matrix.python }}" in artifact["with"]["name"]
    setup = next(
        item for item in job["steps"] if "python-version" in item.get("with", {})
    )
    assert setup["with"]["python-version"] == "${{ matrix.python }}"
    assert "--python ${{ matrix.python }}" in _sync(job)
    assert 'if [ "${{ matrix.shard }}" = "1" ]; then' in step["run"]
    # Rebuild with the same compiler as the GCC sanitizer runtime while keeping
    # the controller itself outside the preloaded runtime.
    assert (
        "env -u LD_PRELOAD CC=gcc .venv/bin/python -m tests.tooling.runtime.proc_memory"
        " --sanitize --output .local/proc-memory --tests tests/suites/product/proc"
    ) in step["run"]


def test_every_built_native_module_has_allocator_sanitizer_and_wheel_contracts(
    workflow,
):
    build = ast.parse((REPO_ROOT / "hatch_build.py").read_text())
    declaration = next(
        node
        for node in build.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_NATIVE_MODULES"
            for target in node.targets
        )
    )
    modules = set(ast.literal_eval(declaration.value))
    owners = {module.split(".")[1] for module in modules}
    assert set(verify._NATIVE_MODULES) == {
        module.replace(".", "/") for module in modules
    }
    assert set(wheel_tests.MODULES) == owners
    paths = {f"tests/suites/product/{owner}" for owner in owners}
    for identity in ("native", "native-sanitizers"):
        steps = workflow["jobs"][identity]["steps"]
        tests = next(step for step in steps if step.get("id") == "tests")
        assert set(re.findall(r"tests/suites/product/[a-z]+", tests["run"])) == paths
        assert all(
            module in "\n".join(step.get("run", "") for step in steps)
            for module in modules
        )


@pytest.mark.parametrize("change", [None, "inactive", "missing", "version", "patch"])
def test_strict_linux_native_preflight_requires_active_reviewed_isal(workflow, change):
    command = next(
        step["run"]
        for step in workflow["jobs"]["native"]["steps"]
        if step.get("name") == "Require active ISA-L in the strict Linux x86 build"
    )
    assert "uv run --no-sync python - <<'PY'" in command
    script = command.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    vendor = REPO_ROOT / "native/compress/vendor/isa-l/UPSTREAM.json"
    patch = REPO_ROOT / "native/compress/src/isal_patch.json"
    configuration = {
        "ISAL_STREAMING": True,
        "ISAL_RUNTIME_VERSION": json.loads(vendor.read_text())["version"],
        "ISAL_PATCH_SHA256": hashlib.sha256(patch.read_bytes()).hexdigest(),
    }
    if change == "inactive":
        configuration["ISAL_STREAMING"] = False
    elif change == "missing":
        del configuration["ISAL_STREAMING"]
    elif change == "version":
        configuration["ISAL_RUNTIME_VERSION"] = "unreviewed"
    elif change == "patch":
        configuration["ISAL_PATCH_SHA256"] = "0" * 64
    result = run_python(
        "import json, sys\n"
        "from types import ModuleType, SimpleNamespace\n"
        "module = ModuleType('unstd.compress')\n"
        "module._gzip_native = SimpleNamespace(**json.loads(sys.argv[1]))\n"
        "sys.modules['unstd.compress'] = module\n" + script,
        [json.dumps(configuration)],
        cwd=REPO_ROOT,
        timeout=30,
    )
    if change is None:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["isal_streaming"] is True
    else:
        assert result.returncode != 0
        assert "ISA-L" in result.stderr


def test_sanitizer_build_and_proc_audit_use_the_preloaded_compiler_runtime(workflow):
    steps = workflow["jobs"]["native-sanitizers"]["steps"]
    build = next(
        step
        for step in steps
        if "uv pip install " in step.get("run", "") and "--editable ." in step["run"]
    )
    assert build["env"]["CC"] == "gcc"
    for name in ("CFLAGS", "LDFLAGS"):
        assert "-fsanitize=address,undefined" in shlex.split(build["env"][name])
    execute = next(step for step in steps if step.get("id") == "tests")
    assert 'LD_PRELOAD="$(gcc -print-file-name=libasan.so)"' in execute["run"]
    # The proc runner compiles another instrumented extension. It must select
    # the same compiler while its uninstrumented controller starts without ASan.
    audit = next(
        line.strip()
        for line in execute["run"].splitlines()
        if "-m tests.tooling.runtime.proc_memory" in line
    )
    assert shlex.split(audit)[:5] == [
        "env",
        "-u",
        "LD_PRELOAD",
        "CC=gcc",
        ".venv/bin/python",
    ]


def test_parallel_type_matrix_runs_every_combination_and_preserves_failure(
    workflow, tmp_path, monkeypatch
):
    command = next(
        step["run"]
        for step in workflow["jobs"]["lint"]["steps"]
        if step.get("name") == "Check type contracts"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / "uv"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['TYPE_MATRIX_CALLS']).open('a') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "python = args[args.index('--python-version') + 1]\n"
        "platform = args[args.index('--platform') + 1]\n"
        "raise SystemExit(23 if (python, platform) == ('3.13', 'darwin') else 0)\n"
    )
    executable.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("TYPE_MATRIX_CALLS", str(calls))
    shell = shutil.which("bash")
    assert shell is not None
    result = subprocess.run(  # noqa: S603 — reviewed workflow with fixture-only tool
        [shell, "-e", "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 1, result.stderr
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(observed) == 12
    assert {
        (args[args.index("--python-version") + 1], args[args.index("--platform") + 1])
        for args in observed
    } == {
        (python, platform)
        for python in ("3.12", "3.13", "3.14", "3.15")
        for platform in ("linux", "darwin", "win32")
    }
    assert len({args[args.index("--cache-dir") + 1] for args in observed}) == 12
    assert all(args[:3] == ["run", "--no-sync", "mypy"] for args in observed)


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


def test_required_linux_archive_consumers_provision_the_native_runtime(workflow):
    for identity, condition in (
        ("test", "matrix.environment.id != 'fallback'"),
        ("extras", "matrix.install.id == 'archives'"),
    ):
        steps = workflow["jobs"][identity]["steps"]
        native = next(step for step in steps if "libarchive-dev" in step.get("run", ""))
        assert native["if"] == condition
        assert native["run"].splitlines() == [
            "sudo apt-get update",
            "sudo apt-get install --yes --no-install-recommends libarchive-dev",
        ]
        assert not native.get("continue-on-error", False)
        assert steps.index(native) < next(
            index
            for index, step in enumerate(steps)
            if step.get("run", "").startswith("uv sync ")
        )


@pytest.mark.parametrize(
    ("filename", "identity", "condition"),
    [
        ("ci", "native-rust", "runner.os == 'Linux'"),
        (
            "ci",
            "test",
            "matrix.environment.id != 'fallback' || matrix.group.id == 'packaging'",
        ),
        ("ci", "extras", None),
        ("ci", "resources", "runner.os == 'Linux'"),
        ("ci", "native", None),
        ("ci", "native-sanitizers", None),
        ("static-analysis", "codeql", "matrix.language == 'c-cpp'"),
        ("performance", "compare", "runner.os == 'Linux'"),
        ("fuzz", "campaign", None),
        ("chaos", "campaign", "runner.os == 'Linux'"),
        ("concurrency", "campaign", "runner.os == 'Linux'"),
    ],
)
def test_linux_native_builds_provision_the_assembler_before_compiling(
    filename, identity, condition
):
    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    workflow = yaml.load((REPO_ROOT / f".github/workflows/{filename}.yml").read_text())
    steps = workflow["jobs"][identity]["steps"]
    assembler = next(
        step
        for step in steps
        if "sudo apt-get install --yes --no-install-recommends yasm"
        in step.get("run", "")
    )
    assert assembler.get("if") == condition
    assert "sudo apt-get update" in assembler["run"].splitlines()
    assert assembler["run"].splitlines()[-1] == "yasm --version"
    assert not assembler.get("continue-on-error", False)
    compilation = next(
        index
        for index, step in enumerate(steps)
        if "--reinstall-package unstd" in step.get("run", "")
        or "uv build " in step.get("run", "")
        or (
            "uv pip install " in step.get("run", "")
            and ("--editable ." in step["run"] or "--target " in step["run"])
        )
    )
    assert steps.index(assembler) < compilation


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
    # The proc memory runner builds its audited extension in this interpreter;
    # pip's isolated editable build does not install setuptools into the venv.
    assert "--group build" in _sync(sanitizer)
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
    # Interpreter preflights can run before restoring the canonical lock.
    restore_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("run", "").strip() == "git restore --source=HEAD -- uv.lock"
    )
    restore = steps[restore_index]
    assert sync < restore_index
    assert restore["if"] == "matrix.environment.id == 'floors'"
    command = shlex.split(restore["run"])
    assert command == ["git", "restore", "--source=HEAD", "--", "uv.lock"]
    assert not restore.get("continue-on-error", False)
    assert restore_index < next(
        index
        for index, step in enumerate(steps)
        if step.get("run", "").startswith("uv build ")
    )
    assert restore_index < next(
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


def test_release_requires_complete_ci_and_verified_artifacts_before_publication(
    workflow,
):
    yaml = require_backend("ruamel.yaml")
    release = yaml.YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".github/workflows/release.yml").read_text()
    )
    assert "workflow_call" in workflow["on"]
    jobs = release["jobs"]
    assert jobs["gate"]["uses"] == "./.github/workflows/ci.yml"
    assert set(jobs["pypi"]["needs"]) == {"ci-status", "gate", "verify"}
    assert "!cancelled()" in jobs["pypi"]["if"]
    assert "needs.verify.result == 'success'" in jobs["pypi"]["if"]
    assert "needs.ci-status.result == 'success'" in jobs["pypi"]["if"]
    assert "needs.gate.result == 'success'" in jobs["pypi"]["if"]
    assert "needs.ci-status.outputs.reused == 'true'" in jobs["pypi"]["if"]
    assert jobs["wheels"]["needs"] == "sdist"
    assert "needs" not in jobs["sdist"]


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
                "PATH": str(Path(sys.executable).parent)
                + os.pathsep
                + os.environ["PATH"],
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
