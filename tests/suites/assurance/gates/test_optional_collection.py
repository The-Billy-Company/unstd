"""Capability admission covers every benchmark gate before optional imports."""

from __future__ import annotations

import sys

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.process import run_python


_PARTIAL = """
from importlib import metadata
from pathlib import Path
import sys
import tomllib
from packaging.requirements import Requirement
import pytest
real = metadata.version
extras = tomllib.loads(Path('pyproject.toml').read_text())['project']['optional-dependencies']
available = {{}}
for requirements in extras.values():
    for text in requirements:
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        available[requirement.name] = next(
            bound.version for bound in requirement.specifier if bound.operator == '>='
        )
def version(name):
    if name in {absent!r}:
        raise metadata.PackageNotFoundError(name)
    if name in available:
        return available[name]
    return real(name)
metadata.version = version
raise SystemExit(pytest.main([
    'tests/suites/assurance/gates/test_optional_collection.py',
    '--collect-only', '-q', *{flags!r},
]))
"""


@pytest.mark.parametrize(
    ("absent", "flags", "status", "message"),
    [
        # The partial lane's own extra is present; every other extra is absent.
        ({"numpy", "rapidfuzz"}, ["--require-extra", "serde"], 0, None),
        ({"msgspec"}, ["--require-extra", "serde"], 4, "msgspec>=0.22"),
        (
            {"msgspec"},
            ["--require-extra", "text", "--require-extra", "serde"],
            4,
            "msgspec",
        ),
        (
            set(),
            ["--require-extra", "nonexistent"],
            4,
            "unknown extras requested: nonexistent",
        ),
        (
            {"rapidfuzz"},
            ["--require-extras"],
            4,
            f"rapidfuzz>={'3.14' if sys.version_info >= (3, 14) else '3.12.0'}",
        ),
    ],
)
def test_partial_extra_admission_fails_only_for_the_named_extras(
    absent: set[str], flags: list[str], status: int, message: str | None
):
    completed = run_python(
        _PARTIAL.format(absent=absent, flags=flags), cwd=REPO_ROOT, timeout=30
    )
    assert completed.returncode == status, (completed.stdout, completed.stderr)
    if message is not None:
        assert message in completed.stderr


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
