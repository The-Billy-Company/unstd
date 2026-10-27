"""Published native artifacts retain source identity without checkout paths."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import hatch_build as build


@pytest.mark.parametrize("compiler", ["unix", "mingw32", "msvc"])
def test_source_remapping_preserves_existing_strict_flags(
    monkeypatch, tmp_path, compiler
):
    strict = (
        ["/fp:strict"]
        if compiler == "msvc"
        else ["-fno-fast-math", "-ffp-contract=off"]
    )
    extension = build.Extension(
        "unstd.text._text_native", ["adapter.c"], extra_compile_args=strict.copy()
    )
    command = build._IsalBuildExt(build.Distribution({"ext_modules": [extension]}))
    command.ensure_finalized()
    command.compiler = SimpleNamespace(compiler_type=compiler)
    command.build_temp = str(tmp_path / "objects")
    command.unstd_source_root = str(tmp_path / "private-build-origin")
    delegated = []
    monkeypatch.setattr(
        build.build_ext, "build_extensions", lambda self: delegated.append(self)
    )
    command.build_extensions()
    prefix = "/pathmap:" if compiler == "msvc" else "-ffile-prefix-map="
    assert extension.extra_compile_args == [
        *strict,
        f"{prefix}{Path(command.unstd_source_root).resolve()}=.",
    ]
    assert delegated == [command]
    if build.sys.platform == "darwin" and compiler != "msvc":
        assert extension.extra_link_args == [
            "-Xlinker",
            "-oso_prefix",
            "-Xlinker",
            str(
                Path(command.build_temp)
                / str(Path(command.unstd_source_root).resolve()).lstrip("/")
            )
            + "/",
        ]
    else:
        assert extension.extra_link_args == []


def test_compiled_macro_and_binary_omit_private_source_prefix(tmp_path):
    project = tmp_path / "private-build-origin"
    source = project / "native" / "adapter.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#include <Python.h>\n"
        'static struct PyModuleDef definition = {PyModuleDef_HEAD_INIT, "_path_proof", NULL, -1, NULL};\n'
        "PyMODINIT_FUNC PyInit__path_proof(void) {\n"
        "  PyObject *module = PyModule_Create(&definition);\n"
        '  if (module && PyModule_AddStringConstant(module, "source_path", __FILE__) < 0) { Py_DECREF(module); return NULL; }\n'
        "  return module;\n}\n"
    )
    extension = build.Extension("_path_proof", [str(source)])
    command = build._IsalBuildExt(build.Distribution({"ext_modules": [extension]}))
    command.unstd_source_root = str(project)
    command.build_lib = str(tmp_path / "lib")
    command.build_temp = str(tmp_path / "objects")
    command.ensure_finalized()
    command.run()
    output = Path(command.get_ext_fullpath(extension.name))
    assert str(project).encode() not in output.read_bytes()
    assert project.name.encode() not in output.read_bytes()
    spec = spec_from_file_location("_path_proof", output)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.source_path.replace("\\", "/").endswith("native/adapter.c")
    assert str(project) not in module.source_path


def test_rust_remapping_preserves_caller_flags(monkeypatch, tmp_path):
    project = tmp_path / "private-build-origin"
    archive = tmp_path / "libunstd_text.a"
    archive.write_bytes(b"fixture static archive")
    received = []

    def cargo(command, **options):
        received.append((command, options))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "reason": "compiler-artifact",
                    "target": {"name": "unstd_text"},
                    "filenames": [str(archive)],
                }
            ),
            stderr="native-static-libs: -lm\n",
        )

    monkeypatch.setenv("RUSTFLAGS", "-C debuginfo=1")
    monkeypatch.setattr(build.subprocess, "run", cargo)
    monkeypatch.setattr(build.CustomBuildHook, "_rust_targets", lambda self: [None])
    hook = build.CustomBuildHook(str(project), {}, None, None, str(tmp_path), "wheel")
    assert hook._build_text(tmp_path / "rust") == (archive, ["-lm"])
    command, options = received[0]
    assert command[-3:] == [
        "--",
        f"--remap-path-prefix={project.resolve()}=.",
        "--print=native-static-libs",
    ]
    assert options == {"capture_output": True, "text": True, "check": False}
    assert os.environ["RUSTFLAGS"] == "-C debuginfo=1"
