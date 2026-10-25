"""Private ISA-L builds preserve compiler admission and authenticated sources."""

from __future__ import annotations

import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from setuptools import Distribution, Extension

import hatch_build as build
from tests.support.environment.anchors import REPO_ROOT


def _config(tmp_path, *, copy_inputs=False):
    vendor = REPO_ROOT / "native/compress/vendor/isa-l"
    helper = REPO_ROOT / "native/compress/src/isal_helpers.c"
    patch = REPO_ROOT / "native/compress/src/isal_patch.json"
    if copy_inputs:
        shutil.copytree(vendor, tmp_path / "vendor")
        shutil.copyfile(helper, tmp_path / "isal_helpers.c")
        shutil.copyfile(patch, tmp_path / "isal_patch.json")
        vendor = tmp_path / "vendor"
        helper = tmp_path / "isal_helpers.c"
        patch = tmp_path / "isal_patch.json"
    return build._IsalConfig(vendor, helper, patch, tmp_path / "build")


def _command(config):
    extension = Extension(
        "unstd.compress._gzip_native",
        ["existing.c"],
        include_dirs=[],
        define_macros=[],
        extra_compile_args=[],
        extra_link_args=[],
        extra_objects=[],
    )
    command = build._IsalBuildExt(Distribution({"ext_modules": [extension]}))
    command.ensure_finalized()
    command.unstd_isal_config = config
    return command, extension


@pytest.mark.parametrize("retained_compiler", [False, True])
@pytest.mark.parametrize(
    "module", ["unstd.compress._gzip_native", "unstd.rand._rand_native"]
)
@pytest.mark.parametrize("reviewed_zlib_rs", [False, True])
def test_real_build_hook_configures_only_the_default_gzip_inflater(
    monkeypatch, tmp_path, retained_compiler, module, reviewed_zlib_rs
):
    hook = build.CustomBuildHook(str(REPO_ROOT), {}, None, None, str(tmp_path), "wheel")
    compiler = {"compiler_so": ["retained-cc"]} if retained_compiler else None
    hook._compiler_commands = compiler
    archive = tmp_path / "reviewed-zlib-rs.a" if reviewed_zlib_rs else None
    monkeypatch.setattr(hook, "_zlib_rs_archive", lambda _path: archive)
    observed = []

    class InspectedBuildError(Exception):
        pass

    def inspect_command(command):
        observed.append(command)
        raise InspectedBuildError

    monkeypatch.setattr(build._IsalBuildExt, "run", inspect_command)
    temporary = tmp_path / "native"
    with pytest.raises(InspectedBuildError):
        hook._build_module(module, temporary, "standard", {"force_include": {}})
    command = observed[0]
    if module == "unstd.compress._gzip_native" and not reviewed_zlib_rs:
        assert command.unstd_isal_config == build._IsalConfig(
            REPO_ROOT / "native/compress/vendor/isa-l",
            REPO_ROOT / "native/compress/src/isal_helpers.c",
            REPO_ROOT / "native/compress/src/isal_patch.json",
            temporary / "reviewed-isa-l",
        )
    else:
        assert command.unstd_isal_config is None
    assert isinstance(command, build._ContainerBuildExt) is retained_compiler
    if retained_compiler:
        assert command.compiler_commands == compiler


@pytest.mark.parametrize(
    ("platform", "target", "eligible"),
    [
        ("linux", "linux-x86_64", True),
        ("linux", "linux-amd64", True),
        ("linux", "linux-aarch64", False),
        ("linux", "linux-i686", False),
        ("linux", "linux-ppc64le", False),
        ("darwin", "macosx-10.13-x86_64", False),
        ("darwin", "macosx-11.0-arm64", False),
        ("win32", "win-amd64", False),
    ],
)
def test_isal_uses_only_the_linux_x86_64_interpreter_target(
    monkeypatch, platform, target, eligible
):
    monkeypatch.setattr(build.sys, "platform", platform)
    monkeypatch.setattr(build.sysconfig, "get_platform", lambda: target)
    assert build._isal_platform() is eligible


def test_build_configuration_supports_an_unregistered_hook_module():
    name = "unstd_isal_unregistered_build_hook"
    assert name not in sys.modules
    spec = spec_from_file_location(name, REPO_ROOT / "hatch_build.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module._IsalConfig(
        *(Path(name) for name in ("vendor", "helper", "patch", "temp"))
    )
    assert config.temporary == Path("temp")
    assert name not in sys.modules


@pytest.mark.parametrize(
    ("versions", "expected"),
    [
        ({}, None),
        ({"nasm": "NASM version 2.11.00"}, None),
        ({"nasm": "NASM version 2.11.01"}, "nasm"),
        ({"nasm": "NASM version 2.16.01", "yasm": "yasm 1.3.0"}, "nasm"),
        ({"nasm": "unrecognized version", "yasm": "yasm 1.3.0"}, "yasm"),
        ({"nasm": "NASM version 2.10.0", "yasm": "yasm 1.3.0"}, "yasm"),
        ({"yasm": "yasm 1.2.0"}, None),
        ({"yasm": "yasm 1.3.0\nCopyright"}, "yasm"),
    ],
)
def test_assembler_admission_checks_versions_and_falls_back(
    monkeypatch, versions, expected
):
    calls = []
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name: f"/fixture/{name}" if name in versions else None,
    )

    def run(argv, **options):
        calls.append(argv)
        assert options == {
            "check": True,
            "text": True,
            "capture_output": True,
            "timeout": 10,
        }
        return SimpleNamespace(stdout=versions[Path(argv[0]).name])

    monkeypatch.setattr(build.subprocess, "run", run)
    admitted = build._isal_assembler()
    assert (Path(admitted[0]).name if admitted else None) == expected
    assert all(argv[1:] == ["--version"] for argv in calls)


def test_staging_applies_frozen_edits_without_changing_retained_originals(tmp_path):
    config = _config(tmp_path)
    manifest = json.loads((config.vendor / "UPSTREAM.json").read_bytes())
    patch = json.loads(config.patch.read_bytes())
    stage = build._stage_isal(config)
    assert len(manifest["files"]) == 20
    assert set(patch["files"]) == {
        "igzip/igzip_inflate.c",
        "igzip/igzip_decode_block_stateless.asm",
    }
    staged = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file()
    }
    assert staged == manifest["files"].keys() | {"isal_helpers.c"}
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((config.vendor / name).read_bytes()).hexdigest() == digest
        expected = patch["files"].get(name, {}).get("after_sha256", digest)
        assert hashlib.sha256((stage / name).read_bytes()).hexdigest() == expected
    assert (stage / "isal_helpers.c").read_bytes() == config.helper.read_bytes()


@pytest.mark.parametrize(
    "name",
    [
        "UPSTREAM.json",
        "LICENSE",
        "igzip/igzip_inflate.c",
        "igzip/igzip_decode_block_stateless.asm",
        "include/igzip_lib.h",
        "include/multibinary.asm",
    ],
)
def test_staging_rejects_changed_original_bytes(tmp_path, name):
    config = _config(tmp_path, copy_inputs=True)
    path = config.vendor / name
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    with pytest.raises(ValueError, match="reviewed input hash mismatch"):
        build._stage_isal(config)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_staging_rejects_a_changed_source_inventory(tmp_path, change):
    config = _config(tmp_path, copy_inputs=True)
    if change == "missing":
        (config.vendor / "include/crc.h").unlink()
    else:
        (config.vendor / "unreviewed.c").write_text("int unreviewed;\n")
    with pytest.raises(ValueError, match="source inventory mismatch"):
        build._stage_isal(config)


@pytest.mark.parametrize("name", ["helper", "patch"])
def test_staging_rejects_changed_owned_support_inputs(tmp_path, name):
    config = _config(tmp_path, copy_inputs=True)
    path = getattr(config, name)
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    with pytest.raises(ValueError, match="reviewed input hash mismatch"):
        build._stage_isal(config)


@pytest.mark.parametrize(
    "change", ["missing_context", "repeated_context", "output", "escape", "schema"]
)
def test_owned_edits_require_exact_context_and_result_hashes(
    monkeypatch, tmp_path, change
):
    config = _config(tmp_path, copy_inputs=True)
    patch = json.loads(config.patch.read_bytes())
    entry = patch["files"]["igzip/igzip_inflate.c"]
    if change == "missing_context":
        entry["replacements"][0]["old"] = "missing reviewed context"
    elif change == "repeated_context":
        entry["replacements"][0]["old"] = " "
    elif change == "output":
        entry["after_sha256"] = "0" * 64
    elif change == "escape":
        patch["files"]["../../escape.c"] = entry
    else:
        patch["schema"] = 2
    payload = json.dumps(patch).encode()
    config.patch.write_bytes(payload)
    # Admit this fixture's patch bytes so its structural/application checks run.
    monkeypatch.setattr(
        build, "_ISAL_PATCH_SHA256", hashlib.sha256(payload).hexdigest()
    )
    with pytest.raises(ValueError, match="ISA-L"):
        build._stage_isal(config)


@pytest.mark.parametrize("required", [False, True])
def test_missing_assembler_preserves_local_native_zlib_but_fails_required_builds(
    monkeypatch, tmp_path, required
):
    command, extension = _command(_config(tmp_path))
    delegated, warnings = [], []
    monkeypatch.setattr(build, "_isal_platform", lambda: True)
    monkeypatch.setattr(build, "_isal_assembler", lambda: None)
    monkeypatch.setattr(
        build.build_ext, "build_extensions", lambda self: delegated.append(self)
    )
    monkeypatch.setattr(command, "warn", warnings.append)
    if required:
        monkeypatch.setenv("CIBUILDWHEEL", "1")
        with pytest.raises(RuntimeError, match=r"needs NASM.*or Yasm"):
            command.build_extensions()
        assert delegated == []
    else:
        monkeypatch.delenv("CIBUILDWHEEL", raising=False)
        command.build_extensions()
        assert delegated == [command]
        assert "native zlib inflater" in warnings[0]
    assert extension.sources == ["existing.c"]
    assert extension.extra_objects == extension.define_macros == []


def test_unconfigured_and_unsupported_builds_do_not_probe_assemblers(
    monkeypatch, tmp_path
):
    delegated = []
    monkeypatch.setattr(
        build.build_ext, "build_extensions", lambda self: delegated.append(self)
    )

    def forbidden():
        pytest.fail("an unsupported or unconfigured build probed an assembler")

    monkeypatch.setattr(build, "_isal_assembler", forbidden)
    command, _ = _command(None)
    command.build_extensions()
    monkeypatch.setattr(build, "_isal_platform", lambda: False)
    command, _ = _command(_config(tmp_path))
    command.build_extensions()
    assert len(delegated) == 2


def test_c_sources_stay_with_setuptools_and_only_assembly_enters_the_archive(
    monkeypatch, tmp_path
):
    config = _config(tmp_path)
    command, extension = _command(config)
    assembler_calls, archived, delegated = [], [], []
    monkeypatch.setattr(build, "_isal_platform", lambda: True)
    monkeypatch.setattr(
        build, "_isal_assembler", lambda: ("/fixture/yasm", "yasm 1.3.0")
    )
    monkeypatch.setattr(
        build.build_ext, "build_extensions", lambda self: delegated.append(self)
    )

    def assemble(argv, **options):
        assert options == {"check": True, "timeout": 30}
        assembler_calls.append(argv)
        Path(argv[-1]).write_bytes(b"fixture assembly object")

    def archive(objects, name, *, output_dir):
        archived.append((objects, name, output_dir))

    command.compiler = SimpleNamespace(
        create_static_lib=archive,
        library_filename=lambda name, *, output_dir: str(
            Path(output_dir) / f"lib{name}.a"
        ),
    )
    monkeypatch.setattr(build.subprocess, "run", assemble)
    command.build_extensions()
    assert delegated == [command]
    assert len(assembler_calls) == 4
    assert {Path(argv[-3]).name for argv in assembler_calls} == {
        "igzip_decode_block_stateless_01.asm",
        "igzip_decode_block_stateless_04.asm",
        "igzip_inflate_multibinary.asm",
        "rfc1951_lookup.asm",
    }
    objects, name, output_dir = archived[0]
    assert len(objects) == 4
    assert all(Path(path).suffix == ".o" for path in objects)
    assert name == "unstd_isal_asm"
    assert Path(output_dir).is_relative_to(config.temporary)
    assert {Path(path).name for path in extension.sources} == {
        "existing.c",
        "igzip_inflate.c",
        "hufftables_c.c",
        "isal_helpers.c",
    }
    assert len(extension.extra_objects) == 1
    assert Path(extension.extra_objects[0]).suffix == ".a"
    assert extension.define_macros == [
        ("UNSTD_WITH_ISAL", None),
        ("UNSTD_ISAL_VERSION", f'"{build._ISAL_VERSION}"'),
        ("UNSTD_ISAL_PATCH_SHA256", f'"{build._ISAL_PATCH_SHA256}"'),
    ]
    assert extension.extra_compile_args == ["-fvisibility=hidden"]
    assert extension.extra_link_args == ["-Wl,--exclude-libs,ALL"]


@pytest.mark.parametrize("required", [False, True])
def test_assembler_failure_cannot_silently_disable_required_builds(
    monkeypatch, tmp_path, required
):
    command, extension = _command(_config(tmp_path))
    delegated = []
    monkeypatch.setattr(build, "_isal_platform", lambda: True)
    monkeypatch.setattr(
        build, "_isal_assembler", lambda: ("/fixture/yasm", "yasm 1.3.0")
    )
    monkeypatch.setattr(
        build.build_ext, "build_extensions", lambda self: delegated.append(self)
    )
    monkeypatch.setattr(command, "warn", lambda _message: None)

    def rejected(*_args):
        raise subprocess.CalledProcessError(1, ["yasm"])

    monkeypatch.setattr(command, "_assemble_isal", rejected)
    if required:
        monkeypatch.setenv("CIBUILDWHEEL", "1")
        with pytest.raises(subprocess.CalledProcessError):
            command.build_extensions()
        assert delegated == []
    else:
        monkeypatch.delenv("CIBUILDWHEEL", raising=False)
        command.build_extensions()
        assert delegated == [command]
    assert extension.sources == ["existing.c"]
    assert extension.extra_objects == extension.define_macros == []
