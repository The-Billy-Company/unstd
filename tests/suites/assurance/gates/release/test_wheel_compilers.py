"""Preserve the real compiler vectors while the admitted runtime owns the SDK."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig

import pytest
from setuptools._distutils.sysconfig import customize_compiler

import hatch_build as build_hook
from tests.tooling.release import wheels


def _write_profile(path, profile):
    path.write_text(json.dumps(profile, sort_keys=True, separators=(",", ":")))


@pytest.fixture
def compiler_profile(monkeypatch, tmp_path):
    profile = json.loads(
        subprocess.check_output(  # noqa: S603 - actual native interpreter, fixed stdlib probe
            [
                Path(sys.executable).resolve(),
                "-I",
                "-S",
                "-c",
                wheels._COMPILER_PROBE,
                *wheels._COMPILER_KEYS,
            ],
            text=True,
        )
    )
    path = tmp_path / "compiler.json"
    _write_profile(path, profile)
    monkeypatch.setenv("CIBUILDWHEEL", "1")
    monkeypatch.setenv("UNSTD_WHEEL_COMPILER_CONFIG", str(path))
    return path, profile


@pytest.mark.skipif(os.name == "nt", reason="Unix container compiler commands")
@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"CFLAGS": "", "CXXFLAGS": "", "LDFLAGS": "", "CPPFLAGS": ""},
        {
            "CC": "cc -fno-fast-math",
            "CFLAGS": "-O3 -fno-fast-math",
            "LDFLAGS": "-Wl,-z,relro",
            "CPPFLAGS": "-DUNSTD_COMPILER_PROOF=1",
        },
        {
            "CC": "cc -fno-fast-math",
            "LDSHARED": "cc -shared",
            "AR": "ar",
            "ARFLAGS": "rcs",
        },
    ],
)
def test_actual_setuptools_vectors_keep_unset_custom_and_empty_caller_flags(
    monkeypatch, compiler_profile, flags
):
    assert set(wheels._COMPILER_KEYS) == build_hook._COMPILER_KEYS
    for name in (*wheels._COMPILER_KEYS, "CPP", "CXXFLAGS", "RANLIB"):
        monkeypatch.delenv(name, raising=False)
    for name, value in flags.items():
        monkeypatch.setenv(name, value)
    compiler = build_hook.new_compiler(compiler="unix")
    customize_compiler(compiler)
    original = {name: getattr(compiler, name) for name in compiler.executables}
    environment = dict(os.environ)
    assert build_hook._compiler_commands() == original
    assert dict(os.environ) == environment


@pytest.mark.parametrize("active", [None, "0", "true"])
def test_ordinary_build_does_not_read_an_opt_in_compiler_profile(monkeypatch, active):
    monkeypatch.setenv("UNSTD_WHEEL_COMPILER_CONFIG", "/missing/compiler.json")
    if active is None:
        monkeypatch.delenv("CIBUILDWHEEL", raising=False)
    else:
        monkeypatch.setenv("CIBUILDWHEEL", active)
    assert build_hook._compiler_commands() is None


def test_wheel_build_without_the_opt_in_profile_keeps_its_normal_compiler(monkeypatch):
    monkeypatch.setenv("CIBUILDWHEEL", "1")
    monkeypatch.delenv("UNSTD_WHEEL_COMPILER_CONFIG", raising=False)
    assert build_hook._compiler_commands() is None


@pytest.mark.skipif(os.name == "nt", reason="Unix container startup hooks")
def test_original_site_hooks_cannot_preload_a_different_setuptools(
    compiler_profile, tmp_path
):
    path, _ = compiler_profile
    original = tmp_path / "original"
    subprocess.run(  # noqa: S603 - actual interpreter, isolated copied environment
        [sys.executable, "-m", "venv", "--without-pip", "--copies", original],
        check=True,
    )
    site = (
        original
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    marker = tmp_path / "old-site-ran"
    (site / "untrusted.pth").write_text(
        f"import pathlib; pathlib.Path({str(marker)!r}).write_text('old site ran')\n"
    )
    python = original / "bin/python"
    subprocess.run([python, "-I", "-c", "pass"], check=True)  # noqa: S603 - controlled positive startup-hook witness
    assert marker.read_text() == "old site ran"
    marker.unlink()
    profile = json.loads(
        subprocess.check_output(  # noqa: S603 - isolated interpreter, fixed stdlib probe
            [python, "-I", "-S", "-c", wheels._COMPILER_PROBE, *wheels._COMPILER_KEYS],
            text=True,
        )
    )
    _write_profile(path, profile)
    assert build_hook._compiler_commands() is not None
    assert not marker.exists()


@pytest.mark.parametrize(
    "fault",
    ["extra", "abi", "version", "schema", "compiler", "nul", "duplicate", "oversized"],
)
def test_invalid_compiler_profile_fails_before_using_its_interpreter(
    compiler_profile, fault
):
    path, profile = compiler_profile
    if fault == "extra":
        profile["unexpected"] = "value"
    elif fault == "abi":
        profile["soabi"] = "different-libc-or-threading-abi"
    elif fault == "version":
        profile["version"] = [0, 0]
    elif fault == "schema":
        profile["schema"] = True
    elif fault == "compiler":
        profile["compiler"]["UNKNOWN"] = "compiler"
    elif fault == "nul":
        profile["compiler"]["CFLAGS"] = "\0"
    _write_profile(path, profile)
    if fault == "duplicate":
        path.write_text('{"schema":0,' + path.read_text()[1:])
    elif fault == "oversized":
        path.write_text(" " * (1 << 17))
    with pytest.raises(
        ValueError, match=r"invalid original wheel compiler profile|Expecting value"
    ):
        build_hook._compiler_commands()


@pytest.mark.skipif(os.name == "nt", reason="Unix container compiler commands")
def test_original_python_sdk_flags_are_rejected(monkeypatch, compiler_profile):
    _, profile = compiler_profile
    monkeypatch.setenv("CPPFLAGS", "-I" + profile["prefix"] + "/include/python")
    with pytest.raises(ValueError, match="commands disagree"):
        build_hook._compiler_commands()


@pytest.mark.skipif(os.name == "nt", reason="Unix container compiler commands")
def test_real_extension_uses_original_commands_and_managed_sdk(
    monkeypatch, compiler_profile, tmp_path
):
    for name in (*wheels._COMPILER_KEYS, "CPP", "CXXFLAGS", "RANLIB"):
        monkeypatch.delenv(name, raising=False)
    commands = build_hook._compiler_commands()
    assert commands is not None
    source = tmp_path / "extension.c"
    source.write_text(
        '#include <Python.h>\nstatic struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_compiler_proof", NULL, -1, NULL};\nPyMODINIT_FUNC PyInit__compiler_proof(void) { return PyModule_Create(&module); }\n'
    )
    extension = build_hook.Extension("_compiler_proof", [str(source)])
    command = build_hook._ContainerBuildExt(
        build_hook.Distribution({"ext_modules": [extension]})
    )
    command.compiler_commands = commands
    command.build_lib = str(tmp_path / "lib")
    command.build_temp = str(tmp_path / "objects")
    command.ensure_finalized()
    command.run()
    assert sysconfig.get_path("include") in command.compiler.include_dirs
    assert {name: getattr(command.compiler, name) for name in commands} == commands
    for name, value in commands.items():
        if value is not None:
            assert getattr(command.compiler, name) is not value
    spec = spec_from_file_location(
        "_compiler_proof", command.get_ext_fullpath("_compiler_proof")
    )
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__name__ == "_compiler_proof"
