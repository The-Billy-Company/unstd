"""Retained process sources keep independent globals and restore import state."""

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[5]
_SPEC = importlib.util.spec_from_file_location(
    "_proc_paired_gate", ROOT / "native/proc/experiments/paired.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
paired = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = paired
_SPEC.loader.exec_module(paired)


def _source(directory, marker, *, detached=False, legacy=False):
    package = directory / "unstd/proc"
    package.mkdir(parents=True)
    if legacy:
        # Retained snapshots keep the original package organization.
        implementation = package / "_internal"
        implementation.mkdir()
        (implementation / "__init__.py").write_text("")
        module = "unstd.proc._internal.backend"
    else:
        implementation = package
        module = "unstd.proc.backend"
    (implementation / "backend.py").write_text(
        f"class Popen:\n    marker = {marker!r}\n"
    )
    code = f"from {module} import Popen as _Popen\n"
    if detached:
        code += "_Popen = object()\n"
    code += "def run(argv):\n    return _Popen.marker\n"
    (package / "__init__.py").write_text(code)
    return directory


@pytest.mark.parametrize("legacy", [False, True])
def test_retained_original_cannot_resolve_new_backend_after_second_load(
    tmp_path, legacy
):
    before = paired._modules()
    old = paired._load(_source(tmp_path / "old", "original", legacy=legacy))
    current = paired._load(_source(tmp_path / "current", "current", legacy=legacy))
    assert old["unstd.proc"].run([]) == "original"
    assert current["unstd.proc"].run([]) == "current"
    module = "unstd.proc._internal.backend" if legacy else "unstd.proc.backend"
    assert old[module].Popen is not current[module].Popen
    assert paired._modules() == before


def test_provenance_rejection_restores_prior_import_state(tmp_path):
    before = paired._modules()
    source = _source(tmp_path / "detached", "fixture", detached=True)
    with pytest.raises(RuntimeError, match="detached"):
        paired._load(source)
    assert paired._modules() == before


@pytest.mark.parametrize("mode", ["candidate", "spawn", "io"])
def test_native_stage_proxy_never_mutates_shared_extension_module(mode):
    from types import ModuleType

    extension = ModuleType("fixture_extension")
    vars(extension).update(__file__="/fixture/native.so", spawn=abs, communicate=len)
    backend = ModuleType("fixture_backend")
    vars(backend)["_native"] = extension
    vars(backend)["_SPAWN_NATIVE"] = False
    proxy = paired._native_proxy(backend, mode)
    assert proxy is not extension
    assert backend._native is proxy
    assert proxy.__file__ == extension.__file__
    assert backend._SPAWN_NATIVE is (mode == "spawn")
    if mode == "io":
        assert proxy.spawn(-1) is NotImplemented
    else:
        assert proxy.spawn(-1) == 1
    if mode == "spawn":
        assert proxy.communicate("text") is NotImplemented
    else:
        assert proxy.communicate("text") == 4
    assert extension.spawn is abs
    assert extension.communicate is len
    proxy.spawn = lambda *_args: "instrumented"
    assert extension.spawn is abs
