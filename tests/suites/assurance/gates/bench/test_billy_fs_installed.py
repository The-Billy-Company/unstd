"""Installed fs timing requires the selected binary and proven public routing."""

from __future__ import annotations

import hashlib
from importlib.machinery import ExtensionFileLoader
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

from bench.profiles import billy_fs_installed as profile
from tests.suites.assurance.gates.bench.test_billy_fs import captured as captured
from unstd import fs


@pytest.mark.parametrize("filename", [None, "absent"])
def test_support_modules_require_file_provenance(filename):
    module = ModuleType("support")
    if filename is None:
        module.__file__ = None
    with pytest.raises(RuntimeError, match="support module has no file provenance"):
        profile._module_path(module)


def test_support_module_provenance_resolves_the_real_file(tmp_path):
    module = ModuleType("support")
    module.__file__ = str(tmp_path / "driver.py")
    assert profile._module_path(module) == (tmp_path / "driver.py").resolve()


def _prepared(tmp_path, *, native_kind="accepted"):
    path = tmp_path / "state"
    expected = b"new complete bytes"

    def setup(_label):
        path.write_bytes(b"wrong sentinel")

    def check(_label, result):
        assert result == path
        assert path.read_bytes() == expected

    def fallback():
        path.write_bytes(expected)
        return path

    def native():
        if native_kind != "dark":
            len(expected)
        if native_kind == "declined":
            return fallback()
        if native_kind != "noop":
            path.write_bytes(expected)
        return path

    return SimpleNamespace(
        setup=setup, check=check, calls={"python": fallback, "native": native}
    ), fallback.__code__


def test_route_proof_observes_native_c_call_and_forced_python_fallback(tmp_path):
    prepared, code = _prepared(tmp_path)
    previous = sys.getprofile()
    proof = profile._route_proof(prepared, len, (code,))
    assert proof == {
        "python": {"native_calls": 0, "fallback_calls": 1},
        "native": {"native_calls": 1, "fallback_calls": 0},
    }
    assert sys.getprofile() is previous


@pytest.mark.parametrize("kind", ["dark", "declined"])
def test_dark_or_declined_native_public_calls_cannot_support_timing(tmp_path, kind):
    prepared, code = _prepared(tmp_path, native_kind=kind)
    previous = sys.getprofile()
    with pytest.raises(RuntimeError, match="route was not accepted for native"):
        profile._route_proof(prepared, len, (code,))
    assert sys.getprofile() is previous


def test_native_noop_cannot_borrow_preceding_successful_python_output(tmp_path):
    prepared, code = _prepared(tmp_path, native_kind="noop")
    previous = sys.getprofile()
    with pytest.raises(AssertionError):
        profile._route_proof(prepared, len, (code,))
    assert prepared.calls["python"]() == tmp_path / "state"
    assert sys.getprofile() is previous


def test_missing_or_noncompiled_native_backend_is_rejected(tmp_path):
    module = ModuleType("selected.fs")
    module._native = None
    with pytest.raises(RuntimeError, match="no active Linux fs backend"):
        profile._native_evidence(module, tmp_path)
    path = tmp_path / "unstd" / "fs" / "_fs_native.so"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not an ELF object")
    module._native = SimpleNamespace(
        __file__=str(path),
        __spec__=importlib.util.spec_from_loader(
            "unstd.fs._fs_native", ExtensionFileLoader("unstd.fs._fs_native", str(path))
        ),
        atomic_write=lambda *_args: None,
    )
    with pytest.raises(RuntimeError, match="not the installed compiled entrypoint"):
        profile._native_evidence(module, tmp_path)


def test_ambient_binary_outside_selected_installation_is_rejected(tmp_path):
    module = ModuleType("selected.fs")
    module._native = SimpleNamespace(__file__=str(tmp_path / "ambient.so"))
    with pytest.raises(RuntimeError, match="escaped the selected installation"):
        profile._native_evidence(module, tmp_path)


def test_pair_forces_only_the_independent_fallback_module(monkeypatch, tmp_path):
    native = ModuleType("native.fs")
    python = ModuleType("python.fs")
    backend = object()
    native._native = backend
    python._native = backend
    evidence = {
        "source": str(tmp_path),
        "files_sha256": {"unstd/fs/__init__.py": "same bytes"},
        "public_signatures": {"atomic_write": "(path, data)"},
    }
    selected = iter(((native, evidence), (python, evidence)))
    monkeypatch.setattr(profile._BASE, "_load_source", lambda _path: next(selected))
    monkeypatch.setattr(profile, "_native_evidence", lambda *_args: {"compiled": True})
    fallback, active, receipt = profile._load_pair(tmp_path)
    assert fallback is python
    assert active is native
    assert python._native is None
    assert native._native is backend
    assert receipt["sources"]["native"] == receipt["sources"]["python"]


def test_shared_module_state_is_rejected_before_disabling_native(monkeypatch, tmp_path):
    module = ModuleType("shared.fs")
    backend = object()
    module._native = backend
    monkeypatch.setattr(profile._BASE, "_load_source", lambda _path: (module, {}))
    monkeypatch.setattr(profile, "_native_evidence", lambda *_args: {"compiled": True})
    with pytest.raises(RuntimeError, match="share mutable module state"):
        profile._load_pair(tmp_path)
    assert module._native is backend


def test_wheel_provenance_rejects_different_installed_fs_bytes(tmp_path):
    wheel = tmp_path / "selected.whl"
    relative = "unstd/fs/_fs_native.cpython-314-aarch64-linux-gnu.so"
    expected = {"files_sha256": {relative: hashlib.sha256(b"selected").hexdigest()}}
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(relative, b"another binary")
    with pytest.raises(RuntimeError, match="differs from installed fs file"):
        profile._wheel_evidence(wheel, expected)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(relative, b"selected")
    assert profile._wheel_evidence(wheel, expected)["sha256"] == profile._sha(wheel)


def test_public_native_preparation_keeps_text_at_public_api_boundary(
    captured, tmp_path
):
    billy, fixture_path = captured
    fixture = profile._BILLY._load_fixtures(fixture_path, billy)
    fixture["cases"] = fixture["cases"][:1]
    reached = []

    def public(path, value, *, durable=False):
        reached.append(type(value))
        return fs.atomic_write(path, value, durable=durable)

    prepared = profile._BILLY._prepare(
        fixture,
        billy,
        tmp_path / "work",
        fs,
        SimpleNamespace(atomic_write=public),
        public_native=True,
    )[0]
    prepared.setup("native")
    prepared.check("native", prepared.calls["native"]())
    assert reached == [str]


def test_direct_help_imports_only_stdlib_without_a_selected_installation(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed local interpreter and script, no shell
        [sys.executable, "-S", str(Path(profile.__file__).resolve()), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--wheel" in result.stdout
    assert "--source" in result.stdout
