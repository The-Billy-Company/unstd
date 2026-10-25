"""The admitted provider must own the same Linux ABI and real interpreter link."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import sysconfig

from cibuildwheel.options import CommandLineArguments, compute_options
from cibuildwheel.platforms import linux
import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.release import wheels


def _native_configurations():
    arguments = CommandLineArguments.defaults()
    arguments.package_dir = REPO_ROOT
    options = compute_options("linux", arguments, {})
    return [
        item
        for item in linux.all_python_configurations()
        if options.globals.build_selector(item.identifier)
        and item.identifier.endswith("-manylinux_" + platform.machine())
    ]


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux runtime provider")
@pytest.mark.parametrize(
    "configuration", _native_configurations(), ids=lambda item: item.identifier
)
def test_actual_linux_runtime_is_admitted_before_its_interpreter_link_changes(
    monkeypatch, tmp_path, configuration
):
    monkeypatch.setenv("UNSTD_WHEEL_COMPILER_CONFIG", str(tmp_path / "compiler.json"))
    minor = configuration.version
    identifier = configuration.identifier.partition("-")[0]
    python = wheels._managed_python(minor, f"linux_{platform.machine()}")
    prefix = python.parent.parent
    original = tmp_path / "original"
    subprocess.run(  # noqa: S603 - admitted interpreter, isolated local venv
        [python, "-m", "venv", "--without-pip", original], check=True
    )
    directory = tmp_path / "python"
    directory.mkdir()
    link = directory / configuration.path.name
    link.symlink_to(original, target_is_directory=True)
    original_files = tuple(original.iterdir())
    wheels._linux_runtime(identifier, directory)
    assert link.resolve() == prefix.resolve()
    assert tuple(original.iterdir()) == original_files
    policy = REPO_ROOT / "tests/tooling/runtime/tls.py"
    report = json.loads(
        subprocess.check_output(  # noqa: S603 - real registered interpreter
            [link / "bin/python", "-I", "-c", wheels._PROBE, policy], text=True
        )
    )
    assert report["errors"] == []
    assert report["version_info"] == [int(part) for part in minor.split(".")]
    assert report["gil_disabled"] is False
    assert report["soabi"].startswith("cpython-" + identifier[2:] + "-")


@pytest.mark.parametrize("identifier", ["cp3", "cp314t", "pp314", "cp3/14", "cp٣١٤"])
def test_invalid_or_free_threaded_linux_identifiers_cannot_change_prefix(
    monkeypatch, tmp_path, identifier
):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="standard CPython build identifier"):
        wheels._linux_runtime(identifier, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "layout",
    [
        "directory",
        "missing",
        pytest.param(
            "dangling",
            marks=pytest.mark.skipif(sys.platform == "win32", reason="Unix links"),
        ),
    ],
)
def test_container_layout_drift_fails_without_replacing_original(
    monkeypatch, tmp_path, layout
):
    monkeypatch.setattr(sys, "platform", "linux")
    link = tmp_path / "cp314-cp314"
    if layout == "directory":
        link.mkdir()
    elif layout == "dangling":
        link.symlink_to(tmp_path / "absent", target_is_directory=True)
    before = tuple(tmp_path.iterdir())
    with pytest.raises(RuntimeError, match="container interpreter layout"):
        wheels._linux_runtime("cp314", tmp_path)
    assert tuple(tmp_path.iterdir()) == before


@pytest.mark.skipif(sys.platform == "win32", reason="Unix interpreter links")
def test_failed_admission_keeps_the_original_prefix(monkeypatch, tmp_path):
    identifier = f"cp{sys.version_info.major}{sys.version_info.minor}"
    original = Path(sys.executable).parent.parent
    link = tmp_path / f"{identifier}-{identifier}"
    link.symlink_to(original, target_is_directory=True)

    def refuse(*args):
        message = "injected provider admission failure"
        raise RuntimeError(message)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(wheels, "_managed_python", refuse)
    with pytest.raises(RuntimeError, match="injected provider admission failure"):
        wheels._linux_runtime(identifier, tmp_path)
    assert link.resolve() == original.resolve()
    assert list(tmp_path.iterdir()) == [link]


@pytest.mark.parametrize(
    "soabi",
    ["gnu", "cpython-314-other-linux-gnu", "cpython-314-x86_64-linux-unknown"],
)
def test_unknown_native_abi_is_rejected_before_any_provider_download(
    monkeypatch, soabi
):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="native Python ABI before provisioning"):
        wheels._managed_python("3.14", "linux_x86_64", soabi)


def test_native_abi_comparison_rejects_a_different_libc_or_threading_abi():
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    report = {
        "errors": [],
        "implementation": "cpython",
        "version_info": list(sys.version_info[:2]),
        "machine": platform.machine(),
        "bits": struct.calcsize("P") * 8,
        "platform": sysconfig.get_platform(),
        "gil_disabled": False,
        "soabi": sysconfig.get_config_var("SOABI"),
    }
    target = sysconfig.get_platform().replace("-", "_")
    wheels._validate_runtime(report, minor, target, str(report["soabi"]))
    for invalid in ("cpython-314-x86_64-linux-musl", "cpython-314t-x86_64-linux-gnu"):
        if invalid != report["soabi"]:
            with pytest.raises(RuntimeError, match="admitted native build"):
                wheels._validate_runtime(report, minor, target, invalid)


def test_container_cli_does_not_load_the_host_only_builder():
    script = REPO_ROOT / "tests/tooling/release/wheels.py"
    result = subprocess.run(  # noqa: S603 - fixed repository tool, isolated process
        [sys.executable, "-S", script, "--linux-runtime", "cp314t"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "standard CPython build identifier" in result.stderr
    assert "cibuildwheel" not in result.stderr
