"""Proc memory qualification keeps native execution live and owns its inputs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from tests.tooling.runtime import proc_memory


def test_linux_bootstrap_changes_only_stdlib_controller_fallback(monkeypatch):
    received = []

    class Standard:
        def _execute_child(self, *arguments):
            received.append(arguments)
            return "standard"

    class Native(Standard):
        def _execute_child(self, *arguments):
            return "native"

    module = SimpleNamespace(Popen=Standard, _USE_POSIX_SPAWN=True)
    monkeypatch.setitem(sys.modules, "subprocess", module)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "/fixture/original-python")
    source = proc_memory._BOOTSTRAP.format(
        wrapper="/fixture/audit-python", sanitize=True, runtime="/fixture/libasan.so"
    )
    exec(compile(source, "sanitizer-bootstrap", "exec"), {})  # noqa: S102 — fixed repository bootstrap with a fixture controller
    arguments = [None] * 23
    assert Standard()._execute_child(*arguments) == "standard"
    assert callable(received[-1][2])
    assert Native()._execute_child(*arguments) == "native"
    explicit = object()
    arguments[2] = explicit
    Standard()._execute_child(*arguments)
    assert received[-1][2] is explicit
    assert module._USE_POSIX_SPAWN is False
    assert sys.executable == "/fixture/audit-python"


def test_macos_bootstrap_restores_sanitizer_loader_for_children(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", "/fixture/original-python")
    monkeypatch.delenv("DYLD_INSERT_LIBRARIES", raising=False)
    source = proc_memory._BOOTSTRAP.format(
        wrapper="/fixture/audit-python", sanitize=True, runtime="/fixture/libasan.dylib"
    )
    exec(compile(source, "sanitizer-bootstrap", "exec"), {})  # noqa: S102 — fixed repository bootstrap with a fixture environment
    assert os.environ["DYLD_INSERT_LIBRARIES"] == "/fixture/libasan.dylib"


def test_fingerprints_cover_sources_and_exclude_loaded_binaries(tmp_path):
    (tmp_path / "kernel.c").write_text("original")
    (tmp_path / "wrapper.py").write_text("original")
    (tmp_path / "kernel.so").write_bytes(b"original")
    before = proc_memory._hashes(tmp_path)
    (tmp_path / "kernel.so").write_bytes(b"changed")
    assert proc_memory._hashes(tmp_path) == before
    (tmp_path / "kernel.c").write_text("changed")
    assert proc_memory._hashes(tmp_path) != before
    assert set(before) == {"kernel.c", "wrapper.py"}


def test_standalone_memory_runner_help_requires_no_product_imports(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed local standalone runner
        [sys.executable, "-S", str(Path(proc_memory.__file__).resolve()), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--sanitize" in result.stdout
    assert "--output" in result.stdout
