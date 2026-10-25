"""The pinned builder must use admitted native runtimes without changing its matrix."""

from __future__ import annotations

from importlib import import_module
import json
import os
from pathlib import Path
import platform
import shlex
import struct
import sys
import sysconfig

from cibuildwheel.options import CommandLineArguments, compute_options
import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.release import wheels


@pytest.mark.parametrize("new_version", ["4.3.0", "4.3.2", "5.0.0"])
def test_builder_upgrades_require_provider_contract_review(monkeypatch, new_version):
    monkeypatch.setattr(wheels, "version", lambda name: new_version)
    with pytest.raises(RuntimeError, match="review the managed-runtime adapter"):
        wheels._configure()


def test_installer_contract_rejects_new_arguments_and_changed_defaults():
    def added(configuration, arch=None, provider=None):
        pass

    def changed(configuration, arch="64"):
        pass

    def positional(configuration, /, arch=None):
        pass

    for installer in (added, changed, positional):
        with pytest.raises(RuntimeError, match="installer contract changed"):
            wheels._contract(installer, ("configuration", "arch"), (None,))


def _native_provider_configurations():
    if sys.platform not in {"darwin", "win32"}:
        return [pytest.param(None, marks=pytest.mark.skip(reason="native installers"))]
    name = "macos" if sys.platform == "darwin" else "windows"
    builder = import_module(f"cibuildwheel.platforms.{name}")
    arguments = CommandLineArguments.defaults()
    arguments.package_dir = REPO_ROOT
    options = compute_options(name, arguments, {})
    native_platform = (
        f"macosx_{platform.machine()}"
        if name == "macos"
        else sysconfig.get_platform().replace("-", "_")
    )
    return [
        item
        for item in builder.all_python_configurations()
        if options.globals.build_selector(item.identifier)
        and item.identifier.startswith("cp")
        and "t-" not in item.identifier
        and item.identifier.endswith("-" + native_platform)
        and (name == "macos" or item.version.startswith("3.12."))
    ]


@pytest.mark.parametrize(
    "configuration",
    _native_provider_configurations(),
    ids=lambda item: getattr(item, "identifier", "native-only"),
)
def test_actual_native_provider_passes_tls_and_preserves_builder_catalog(
    monkeypatch, tmp_path, capsys, configuration
):
    name = "macos" if sys.platform == "darwin" else "windows"
    builder = import_module(f"cibuildwheel.platforms.{name}")
    original = builder.install_cpython
    configurations = builder.all_python_configurations()
    monkeypatch.setattr(builder, "install_cpython", original)
    wheels._configure()
    assert builder.install_cpython.__wrapped__ is original
    assert builder.all_python_configurations() == configurations
    if name == "macos":
        executable = builder.install_cpython(
            tmp_path,
            configuration.version,
            configuration.url,
            False,
            configuration.sha256,
        )
    else:
        executable = builder.install_cpython(configuration)
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == []
    assert report["version_info"] == [
        int(part) for part in configuration.version.split(".")[:2]
    ]
    assert report["implementation"] == "cpython"
    assert report["gil_disabled"] is False
    assert Path(report["executable"]).resolve() == executable.resolve()
    assert executable.is_file()


def test_native_runtime_cannot_mask_emulated_or_free_threaded_abis():
    report = {
        "errors": [],
        "implementation": "cpython",
        "version_info": [3, 12],
        "machine": platform.machine(),
        "bits": struct.calcsize("P") * 8,
        "gil_disabled": False,
        "platform": "win-amd64",
    }
    with pytest.raises(RuntimeError, match="admitted native build"):
        wheels._validate_runtime(report, "3.12", "win_arm64")
    report["platform"] = "win-arm64"
    wheels._validate_runtime(report, "3.12", "win_arm64")
    report["gil_disabled"] = True
    with pytest.raises(RuntimeError, match="admitted native build"):
        wheels._validate_runtime(report, "3.12", "win_arm64")


@pytest.mark.parametrize("incoming_flags", [None, "", "-O3 -fno-fast-math"])
@pytest.mark.parametrize("identifier", ["cp312-macosx_x86_64", "cp313-macosx_x86_64"])
@pytest.mark.skipif(os.name == "nt", reason="the native Unix compiler owns macOS flags")
def test_actual_builder_preserves_intel_flags_and_effective_deployment_target(
    monkeypatch, incoming_flags, identifier
):
    from setuptools._distutils.ccompiler import new_compiler
    from setuptools._distutils.sysconfig import customize_compiler

    arguments = CommandLineArguments.defaults()
    arguments.package_dir = REPO_ROOT
    options = compute_options("macos", arguments, {})
    previous = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    }
    if incoming_flags is not None:
        previous.update(CFLAGS=incoming_flags, LDFLAGS="-Wl,-headerpad,40")
    selected = options.build_options(identifier).environment.as_dictionary(
        prev_environment=previous
    )
    assert selected["MACOSX_DEPLOYMENT_TARGET"] == "10.13"
    target = f"-mmacosx-version-min={selected['MACOSX_DEPLOYMENT_TARGET']}"
    original_flags = (
        incoming_flags
        if incoming_flags is not None
        else sysconfig.get_config_var("CFLAGS") or ""
    )
    assert selected["CFLAGS"] == f"{original_flags} {target}"
    assert selected["LDFLAGS"] == f"{previous.get('LDFLAGS', '')} {target}"
    for unaffected in (
        "cp312-macosx_arm64",
        "cp313-macosx_arm64",
        "cp314-macosx_arm64",
        "cp314-macosx_x86_64",
    ):
        assert (
            options.build_options(unaffected).environment.as_dictionary(
                prev_environment=previous
            )
            == previous
        )

    for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CC", "LDSHARED"):
        monkeypatch.delenv(name, raising=False)
    for name in ("CFLAGS", "LDFLAGS", "MACOSX_DEPLOYMENT_TARGET"):
        monkeypatch.setenv(name, selected[name])
    compiler = new_compiler(compiler="unix")
    customize_compiler(compiler)
    expected = shlex.split(selected["CFLAGS"])
    assert any(
        compiler.compiler_so[index : index + len(expected)] == expected
        for index in range(len(compiler.compiler_so) - len(expected) + 1)
    )
    for command in (compiler.compiler_so, compiler.linker_so):
        assert [flag for flag in command if flag.startswith("-mmacosx-version-min=")][
            -1
        ] == target
