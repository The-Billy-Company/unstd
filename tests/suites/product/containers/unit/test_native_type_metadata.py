"""Unusual type namespaces never execute comparisons during native preflight."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


_PROBE = r"""
import json, sys
import sortedcontainers
from unstd.containers import _sorted_native as native

mode, backend = sys.argv[1:]
events = []
armed = False
class NamespaceKey(str):
    def __hash__(self):
        return hash("_unstd_sorted_native_config") if mode != "mro" else str.__hash__(self)
    def __eq__(self, other):
        if armed:
            events.append(str(self))
            raise AssertionError("native preflight compared a namespace key")
        return str.__eq__(self, other)

if mode == "mro":
    class Owned(sortedcontainers.SortedList):
        pass
    if backend == "native":
        native.install(Owned, sortedcontainers.SortedList)
    mixin = type("Mixin", (), {"__slots__": (), NamespaceKey("_load"): 1000})
    Owned.__bases__ = (sortedcontainers.SortedList, mixin)
else:
    Owned = type("Owned", (sortedcontainers.SortedList,), {NamespaceKey("decoy"): None})
    if backend == "native":
        native.install(Owned, sortedcontainers.SortedList)

values = Owned(range(100))
events.clear()
armed = True
if mode == "stats":
    answer = native.mutation_stats(Owned)
elif mode == "hook":
    answer = native._set_test_hook(Owned)
else:
    answer = values.bisect_left(64)
print(json.dumps({"answer": answer, "events": events}))
"""


@pytest.mark.parametrize("mode", ["mro", "config"])
def test_type_namespace_keys_do_not_run_during_read_preflight(
    tmp_path: Path, mode: str
) -> None:
    pytest.importorskip("unstd.containers._sorted_native")
    for backend in ("python", "native"):
        result = run_python(_PROBE, [mode, backend], cwd=tmp_path, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"answer": 64, "events": []}


def test_stats_lookup_ignores_colliding_nonexact_type_keys(tmp_path: Path) -> None:
    pytest.importorskip("unstd.containers._sorted_native")
    result = run_python(_PROBE, ["stats", "native"], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "answer": {
            "delete_fast": 0,
            "delete_fallback": 0,
            "update_fast": 0,
            "update_fallback": 0,
        },
        "events": [],
    }


def test_test_hook_lookup_ignores_colliding_nonexact_type_keys(tmp_path: Path) -> None:
    native = pytest.importorskip("unstd.containers._sorted_native")
    if (
        not hasattr(native, "_set_test_hook")
        and os.environ.get("UNSTD_CONTAINERS_REQUIRE_TEST_HOOKS") != "1"
    ):
        pytest.skip("test-only native hooks are not compiled")
    result = run_python(_PROBE, ["hook", "native"], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"answer": 0, "events": []}
