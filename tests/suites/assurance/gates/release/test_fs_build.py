"""Filesystem acceleration ships only on Linux and remains in wheel coverage."""

from __future__ import annotations

import importlib
import shlex
import sys
import tomllib

import pytest

import hatch_build as build
from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.release import wheel_tests


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("gil_disabled", [False, True])
def test_build_selects_filesystem_only_for_linux_with_the_gil(
    monkeypatch, platform, gil_disabled
):
    attempted = []
    hook = object.__new__(build.CustomBuildHook)
    monkeypatch.setattr(build.sys, "platform", platform)
    monkeypatch.setattr(build, "_compiler_commands", lambda: None)
    configuration = build.sysconfig.get_config_var
    monkeypatch.setattr(
        build.sysconfig,
        "get_config_var",
        lambda name: gil_disabled if name == "Py_GIL_DISABLED" else configuration(name),
    )
    monkeypatch.setattr(
        build.CustomBuildHook,
        "_build_module",
        lambda _self, module, *_args: attempted.append(module),
    )
    try:
        hook.initialize("standard", {"force_include": {}})
    finally:
        if hasattr(hook, "_temporary"):
            hook._temporary.cleanup()
    if gil_disabled:
        assert attempted == []
    else:
        expected = [
            module
            for module in build._NATIVE_MODULES
            if module != "unstd.fs._fs_native" or platform == "linux"
        ]
        assert attempted == expected
    assert ("unstd.fs._fs_native" in attempted) == (
        platform == "linux" and not gil_disabled
    )


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_installed_wheel_preflight_requires_fs_only_on_linux(monkeypatch, platform):
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        commands = tomllib.load(stream)["tool"]["cibuildwheel"]["test-command"]
    command = next(command for command in commands if "unstd.fs._fs_native" in command)
    executable, option, source = shlex.split(command)
    assert (executable, option) == ("python", "-c")
    observed = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(importlib, "import_module", observed.append)
    exec(compile(source, "<fs-wheel-preflight>", "exec"), {})  # noqa: S102 — repository-owned preflight
    assert observed == (["unstd.fs._fs_native"] if platform == "linux" else [])
    assert "fs" in wheel_tests.MODULES


def test_production_build_never_compiles_the_filesystem_experiment():
    source = (REPO_ROOT / "hatch_build.py").read_text()
    assert "native/fs/experiments" not in source
    assert "unstd.fs._fs_native" in build._NATIVE_MODULES
