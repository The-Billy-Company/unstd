"""Required audio qualification binds trusted inputs to the actual loaded DSO."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import shlex
from typing import TYPE_CHECKING, Any, cast
from urllib import request

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend
from tests.tooling.runtime import audio


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    return cast(
        "dict[str, Any]",
        yaml.load((REPO_ROOT / ".github/workflows/audio-security.yml").read_text()),
    )


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", workflow["jobs"]["qualify"]["steps"])


def _step(workflow: dict[str, Any], identity: str) -> dict[str, Any]:
    return next(step for step in _steps(workflow) if step.get("id") == identity)


def _python(step: dict[str, Any]) -> str:
    return str(step["run"]).split("<<'PYTHON'\n", 1)[1].split("\nPYTHON", 1)[0]


def _execute(code: str) -> None:
    # Execute only this repository's stdlib workflow snippet with network mocked
    # in the download tests and isolated temporary cwd in every execution test.
    exec(compile(code, "<audio-security-workflow>", "exec"), {})  # noqa: S102


def test_audio_qualification_is_a_required_read_only_bounded_reusable_job(
    workflow: dict[str, Any],
) -> None:
    assert {"workflow_call", "workflow_dispatch"} <= workflow["on"].keys()
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"qualify"}
    job = workflow["jobs"]["qualify"]
    assert job["runs-on"] == "ubuntu-latest"
    assert 1 <= job["timeout-minutes"] <= 20
    assert job["env"]["UV_PROJECT_ENVIRONMENT"].startswith("${{ runner.temp }}/")
    assert job["defaults"]["run"]["shell"] == "bash"
    for step in _steps(workflow):
        assert not step.get("continue-on-error", False)
        if "uses" in step:
            assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    ci = yaml.load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    gate = ci["jobs"]["audio-security"]
    assert gate["uses"] == "./.github/workflows/audio-security.yml"
    assert gate["permissions"] == {"contents": "read"}
    assert "audio-security" in ci["jobs"]["result"]["needs"]


def test_action_pins_match_ci_and_no_publishing_capabilities_are_requested(
    workflow: dict[str, Any],
) -> None:
    pins = {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "astral-sh/setup-uv": "c18668ad3cf93ea998bef934396af7bb5c839dc7",
        "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    }
    uses = [step for step in _steps(workflow) if "uses" in step]
    assert {step["uses"] for step in uses} == {
        f"{name}@{pin}" for name, pin in pins.items()
    }
    checkout = next(
        step for step in uses if step["uses"].startswith("actions/checkout@")
    )
    assert checkout["with"]["persist-credentials"] is False
    setup = next(
        step for step in uses if step["uses"].startswith("astral-sh/setup-uv@")
    )
    assert setup["with"]["version"] == "0.13.4"
    assert setup["with"]["enable-cache"] is False
    assert "id-token" not in workflow["permissions"]
    assert all("pypi" not in step.get("uses", "").lower() for step in _steps(workflow))


def test_python_dependencies_are_authenticated_before_the_owned_project_build(
    workflow: dict[str, Any],
) -> None:
    steps = _steps(workflow)
    commands = [step.get("run", "") for step in steps]
    trust = next(
        index
        for index, command in enumerate(commands)
        if "supply_chain check" in command
    )
    exports = next(
        index for index, command in enumerate(commands) if "uv export" in command
    )
    install = next(
        index for index, command in enumerate(commands) if "uv venv" in command
    )
    assert trust < exports < install
    lines = commands[exports].splitlines()
    assert len(lines) == 2
    for line in lines:
        tokens = shlex.split(line)
        assert tokens[:3] == ["uv", "export", "--locked"]
        assert {"--no-emit-project", "--no-header", "--output-file"} <= set(tokens)
    assert "--only-group build" in lines[0]
    assert "--extra audio" in lines[1]
    assert "--group dev" in lines[1]
    installs = [
        shlex.split(line)
        for line in commands[install].splitlines()
        if line.startswith("uv pip install")
    ]
    assert len(installs) == 2
    assert {"--no-deps", "--only-binary", ":all:", "--require-hashes"} <= set(
        installs[0]
    )
    assert installs[0].count("--requirements") == 2
    assert {"--no-deps", "--no-index", "--no-build-isolation", "."} <= set(installs[1])


def test_codec_build_prerequisites_are_explicit_and_inputs_are_fixed(
    workflow: dict[str, Any],
) -> None:
    apt = next(
        step["run"]
        for step in _steps(workflow)
        if "apt-get install" in step.get("run", "")
    )
    assert {
        "cmake",
        "pkg-config",
        "patch",
        "libflac-dev",
        "libvorbis-dev",
        "libogg-dev",
        "libopus-dev",
        "libmp3lame-dev",
        "libmpg123-dev",
    } <= set(shlex.split(apt))
    code = _python(_step(workflow, "inputs"))
    syntax = ast.parse(code)
    assignment = next(
        node
        for node in syntax.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "sources"
            for target in node.targets
        )
    )
    assert ast.literal_eval(assignment.value) == (
        ("libsndfile-1.2.2.tar.xz", audio.SOURCE_URL, audio.SOURCE_SHA256),
        ("official.patch", audio.PATCH_URL, audio.PATCH_SHA256),
    )
    assert ".read(64 * 1024 * 1024 + 1)" in code
    assert code.index("hashlib.sha256") < code.index(".write_bytes")


@pytest.fixture(params=[False, True])
def transport_error(request: pytest.FixtureRequest) -> bool:
    return cast("bool", request.param)


def test_fetch_rejects_corruption_or_transport_failure_before_writing_inputs(
    workflow: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport_error: bool,
) -> None:
    calls: list[tuple[str, int]] = []

    def download(url: str, *, timeout: int) -> io.BytesIO:
        calls.append((url, timeout))
        if transport_error:
            msg = "fixture download failed"
            raise OSError(msg)
        return io.BytesIO(b"not the authenticated released archive")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(request, "urlopen", download)
    with pytest.raises((OSError, SystemExit)):
        _execute(_python(_step(workflow, "inputs")))
    assert calls == [(audio.SOURCE_URL, 60)]
    assert not list((tmp_path / ".local/audio-security/inputs").iterdir())


def _staged_wheel(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / ".local/audio-security/build"
    root.mkdir(parents=True)
    wheel = root / "soundfile-0.14.0+unstd.474e4d3-py3-none-linux_x86_64.whl"
    wheel.write_bytes(b"fixture wheel bytes; installation is never invoked")
    provenance = root / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "output": {
                    "wheel": wheel.name,
                    "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                }
            }
        )
    )
    policy = root.parent / "trusted-build.json"
    policy.write_text(
        json.dumps(
            {
                "schema": 1,
                "provenance_sha256": [
                    hashlib.sha256(provenance.read_bytes()).hexdigest()
                ],
            }
        )
    )
    return wheel, provenance, policy


@pytest.fixture(params=["provenance", "wheel", "policy"])
def tamper(request: pytest.FixtureRequest) -> str:
    return cast("str", request.param)


def test_wheel_installation_rejects_untrusted_or_mismatched_bytes(
    workflow: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    wheel, provenance, policy = _staged_wheel(tmp_path)
    target = {"wheel": wheel, "provenance": provenance, "policy": policy}[tamper]
    if tamper == "policy":
        target.write_text('{"schema": 1, "provenance_sha256": []}')
    else:
        target.write_bytes(target.read_bytes() + b"modified")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _execute(_python(_step(workflow, "install")))
    assert not (tmp_path / ".local/audio-security/patched-wheel.txt").exists()


def test_wheel_requirement_binds_the_same_job_bytes_to_hash_enforced_install(
    workflow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel, _, _ = _staged_wheel(tmp_path)
    monkeypatch.chdir(tmp_path)
    install = _step(workflow, "install")
    _execute(_python(install))
    requirement = (tmp_path / ".local/audio-security/patched-wheel.txt").read_text()
    assert (
        requirement
        == wheel.as_uri()
        + " --hash=sha256:"
        + hashlib.sha256(wheel.read_bytes()).hexdigest()
        + "\n"
    )
    command = next(
        line
        for line in install["run"].splitlines()
        if line.startswith("uv pip install")
    )
    tokens = shlex.split(command)
    assert {
        "--no-deps",
        "--no-index",
        "--require-hashes",
        "--reinstall",
        "--requirements",
    } <= set(tokens)
    assert (
        tokens[tokens.index("--requirements") + 1]
        == ".local/audio-security/patched-wheel.txt"
    )


def test_only_the_trusted_builder_emits_policy_and_preflight_gates_real_audio_tests(
    workflow: dict[str, Any],
) -> None:
    steps = _steps(workflow)
    build = _step(workflow, "build")
    tokens = shlex.split(build["run"])
    assert "tests.tooling.runtime.audio_build" in tokens
    assert (
        tokens[tokens.index("--source") + 1]
        == ".local/audio-security/inputs/libsndfile-1.2.2.tar.xz"
    )
    assert (
        tokens[tokens.index("--patch") + 1]
        == ".local/audio-security/inputs/official.patch"
    )
    assert (
        tokens[tokens.index("--trust-policy") + 1]
        == ".local/audio-security/trusted-build.json"
    )
    preflight = _step(workflow, "preflight")
    assert (
        "tests.tooling.runtime.audio --provenance .local/audio-security/build/provenance.json --trust-policy .local/audio-security/trusted-build.json --json"
        in preflight["run"]
    )
    assert "if" not in preflight
    assert "||" not in preflight["run"]
    assert "PYTHONPATH" not in workflow["jobs"]["qualify"]["env"]
    tests = _step(workflow, "tests")
    assert "-m pytest tests/suites/product/audio --require-extra audio" in tests["run"]
    assert "if" not in tests
    assert (
        steps.index(build)
        < steps.index(_step(workflow, "install"))
        < steps.index(preflight)
        < steps.index(tests)
    )


def test_checked_artifacts_are_retained_on_failure_without_publishing(
    workflow: dict[str, Any],
) -> None:
    upload = next(
        step
        for step in _steps(workflow)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    settings = upload["with"]
    assert settings["include-hidden-files"] is True
    assert settings["if-no-files-found"] == "error"
    assert 1 <= settings["retention-days"] <= 30
    assert {
        ".local/audio-security/*.json",
        ".local/audio-security/*.xml",
        ".local/audio-security/build/*.json",
        ".local/audio-security/build/*.sha256",
        ".local/audio-security/build/*.whl",
        ".local/audio-security/build/build.log",
    } <= set(settings["path"].splitlines())
