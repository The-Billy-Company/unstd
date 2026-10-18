"""Capability admission covers every benchmark gate before optional imports."""

from __future__ import annotations

from tests.support.anchors import REPO_ROOT
from tests.support.process import run_python


def test_all_benchmark_gate_modules_are_ignored_when_bench_cannot_import():
    # Block the real mandatory crypto backend in a fresh interpreter. Importing
    # bench (including bench.clone_ids_profile) then follows the genuine base
    # install failure path; no test module may fail during collection first.
    code = """
import importlib.abc
import sys
class MissingBlake3(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'blake3' or fullname.startswith('blake3.'):
            raise ModuleNotFoundError('base capability control blocks blake3')
sys.meta_path.insert(0, MissingBlake3())
import pytest
raise SystemExit(pytest.main([
    'tests/suites/assurance/gates/bench', '--collect-only', '-q'
]))
"""
    completed = run_python(code, cwd=REPO_ROOT, timeout=15)
    assert completed.returncode == 5, (completed.stdout, completed.stderr)
    assert "::test_" not in completed.stdout
    assert "ERROR" not in completed.stdout
