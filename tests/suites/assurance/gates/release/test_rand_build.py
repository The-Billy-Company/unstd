"""The optional shuffle kernel ships and is exercised without NumPy."""

from __future__ import annotations

import shlex
import tomllib
from types import SimpleNamespace

import pytest

import hatch_build as build
from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.release import verify, wheel_tests


_MODULE = "unstd.rand._rand_native"


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("implementation", ["cpython", "pypy"])
@pytest.mark.parametrize("gil_disabled", [False, True])
def test_shuffle_kernel_builds_only_on_gil_cpython(
    monkeypatch, platform, implementation, gil_disabled
):
    attempted = []
    hook = object.__new__(build.CustomBuildHook)
    monkeypatch.setattr(build.sys, "platform", platform)
    monkeypatch.setattr(
        build.sys,
        "implementation",
        SimpleNamespace(**(vars(build.sys.implementation) | {"name": implementation})),
    )
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
    assert (_MODULE in attempted) == (implementation == "cpython" and not gil_disabled)
    assert attempted.count(_MODULE) <= 1
    if implementation != "cpython" or gil_disabled:
        assert attempted == []


def test_shuffle_extension_uses_standalone_registered_c_source(monkeypatch, tmp_path):
    hook = build.CustomBuildHook(str(REPO_ROOT), {}, None, None, str(tmp_path), "wheel")
    received = []

    class InspectedError(Exception):
        pass

    def extension(name, sources, **configuration):
        received.append((name, sources, configuration))
        raise InspectedError

    monkeypatch.setattr(build, "Extension", extension)
    with pytest.raises(InspectedError):
        hook._build_module(_MODULE, tmp_path, "standard", {"force_include": {}})
    assert len(received) == 1
    name, sources, configuration = received[0]
    source = REPO_ROOT / "native/rand/src/adapter.c"
    assert name == _MODULE
    assert sources == [str(source)]
    assert source.is_file()
    for key in ("include_dirs", "libraries", "extra_objects", "extra_link_args"):
        assert configuration[key] == []
    assert "rand" in wheel_tests.MODULES
    assert _MODULE.replace(".", "/") in verify._NATIVE_MODULES


def _preflight():
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        commands = tomllib.load(stream)["tool"]["cibuildwheel"]["test-command"]
    command = next(command for command in commands if _MODULE in command)
    executable, option, source = shlex.split(command)
    assert (executable, option) == ("python", "-c")
    return compile(source, "<rand-wheel-preflight>", "exec")


def test_installed_wheel_preflight_imports_shuffle_without_numpy():
    observed = []

    def import_module(name, *_args):
        observed.append(name)
        return SimpleNamespace()

    exec(_preflight(), {"__builtins__": {"__import__": import_module}})  # noqa: S102 — repository-owned preflight with fixture imports
    assert _MODULE in observed
    assert not any(name == "numpy" or name.startswith("numpy.") for name in observed)


def test_missing_shuffle_extension_fails_installed_wheel_preflight():
    def import_module(name, *_args):
        if name == _MODULE:
            message = "native shuffle missing from installed wheel"
            raise ModuleNotFoundError(message)
        return SimpleNamespace()

    with pytest.raises(ModuleNotFoundError, match="native shuffle missing"):
        exec(_preflight(), {"__builtins__": {"__import__": import_module}})  # noqa: S102 — repository-owned preflight with fixture imports
