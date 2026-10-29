"""The pinned builder must use admitted native runtimes without changing its matrix."""

from __future__ import annotations

from importlib import import_module
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import struct
import subprocess
import sys
import sysconfig
import tomllib

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


def test_existing_provider_is_refreshed_before_real_tls_and_abi_admission(
    monkeypatch, tmp_path, capsys
):
    uv = str(tmp_path / "uv")
    stale = tmp_path / "stale-python"
    current = Path(sys.executable).resolve()
    selected = {"python": stale}
    original_output = subprocess.check_output
    original_run = subprocess.run

    def install(command, *, check, timeout):
        assert command[:4] == [uv, "python", "install", "--managed-python"]
        assert check is True
        assert timeout > 0
        # A provider with an existing minor installation leaves it selected
        # unless the caller requests the latest audited patch release.
        if "--upgrade" in command:
            selected["python"] = current

    def output(command, **options):
        if command[:3] == [uv, "python", "find"]:
            return str(selected["python"]) + "\n"
        return original_output(command, **options)

    monkeypatch.setattr(wheels.shutil, "which", lambda _name: uv)
    monkeypatch.setattr(wheels.subprocess, "check_output", output)

    # check_output internally calls subprocess.run; let the real runtime probe
    # execute through its original implementation after provider installation.
    def run(command, **options):
        if command[0] == uv:
            return install(command, **options)
        return original_run(command, **options)

    monkeypatch.setattr(wheels.subprocess, "run", run)
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    executable = wheels._managed_python.__wrapped__(
        minor, sysconfig.get_platform().replace("-", "_")
    )
    assert executable == current
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == []
    assert Path(report["executable"]).resolve() == current


def test_pinned_provider_catalog_contains_the_development_python_on_release_hosts():
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("the managed runtime catalog requires uv on PATH")
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    (requirement,) = project["dependency-groups"]["release-uv"]
    pinned = requirement.removeprefix("uv==")
    installed = subprocess.check_output(  # noqa: S603 — installed tool, fixed version query
        [uv, "--version"], text=True, timeout=15
    ).split()[1]
    if installed != pinned and not os.environ.get("CI"):
        pytest.skip(f"catalog qualification requires audited uv {pinned}")
    assert installed == pinned
    version = (REPO_ROOT / ".python-version").read_text().strip()
    catalog = subprocess.check_output(  # noqa: S603 — offline bundled provider catalog
        [
            uv,
            "python",
            "list",
            version,
            "--only-downloads",
            "--all-versions",
            "--all-platforms",
            "--all-arches",
            "--output-format",
            "json",
            "--offline",
            "--no-config",
        ],
        text=True,
        timeout=30,
    )
    platforms = {
        (item["os"], item["arch"], item["libc"])
        for item in json.loads(catalog)
        if item["version"] == version
        and item["implementation"] == "cpython"
        and item["variant"] == "default"
    }
    required = {
        (system, architecture, libc)
        for system, libcs in (
            ("linux", ("gnu", "musl")),
            ("macos", ("none",)),
            ("windows", ("none",)),
        )
        for architecture in ("aarch64", "x86_64")
        for libc in libcs
    }
    assert required <= platforms, (pinned, version, required - platforms)


@pytest.mark.parametrize(
    ("name", "platforms"),
    [
        (
            "linux",
            (
                "manylinux_x86_64",
                "manylinux_aarch64",
                "musllinux_x86_64",
                "musllinux_aarch64",
            ),
        ),
        ("macos", ("macosx_x86_64", "macosx_arm64")),
        ("windows", ("win_amd64", "win_arm64")),
    ],
)
def test_builder_catalog_covers_every_advertised_runtime_on_release_platforms(
    name, platforms
):
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    prefix = "Programming Language :: Python :: 3."
    minors = {
        classifier.removeprefix(prefix)
        for classifier in project["project"]["classifiers"]
        if classifier.startswith(prefix)
    }
    assert minors == {"12", "13", "14", "15"}
    builder = import_module(f"cibuildwheel.platforms.{name}")
    arguments = CommandLineArguments.defaults()
    arguments.package_dir = REPO_ROOT
    options = compute_options(name, arguments, {})
    selected = {
        item.identifier
        for item in builder.all_python_configurations()
        if options.globals.build_selector(item.identifier)
    }
    expected = {
        f"cp3{minor}-{platform_tag}" for minor in minors for platform_tag in platforms
    }
    assert expected <= selected, expected - selected


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
        "cp315-macosx_arm64",
        "cp315-macosx_x86_64",
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
