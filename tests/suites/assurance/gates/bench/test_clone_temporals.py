"""Optional temporal measurements retain immutable identity and route proofs."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from bench.profiles import clone_common_cpu as profile
from bench.profiles import clone_cpu as base
from bench.workloads import clone_temporals
from unstd.clone import structural


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


CASES = clone_temporals.workloads()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_temporal_workloads_keep_types_values_and_identity(case: base.Workload) -> None:
    assert profile._check(case, "current", structural.deep(case.source))
    assert profile._check(case, "stdlib", copy.deepcopy(case.source))


def test_temporal_workload_sets_remain_optional_and_values_are_distinct() -> None:
    assert len(profile._workloads()) == 30
    assert len(CASES) == 14
    assert len({case.name for case in CASES}) == len(CASES)
    assert not {case.name for case in CASES} & {
        case.name for case in profile._workloads()
    }
    for case in CASES[:10]:
        assert type(case.source) is list
        assert len(set(case.source)) == len(case.source)
        assert len(case.source) == int(case.name.rsplit("-", 1)[1])


def test_temporal_check_rejects_recreated_leaves_on_both_source_variants() -> None:
    source: list[clone_temporals.Tree] = [dt.date(2026, 1, 1)]
    case = clone_temporals._case("temporal-identity", source)
    recreated = copy.deepcopy(source)
    assert recreated[0] is not source[0]
    assert not profile._check(case, "current", recreated)
    assert not profile._check(case, "before", recreated)
    assert profile._check(case, "stdlib", recreated)
    assert not profile._check(case, "stdlib", source)


def test_temporal_check_keeps_fold_and_exact_leaf_types() -> None:
    stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    case = clone_temporals._case("temporal-fold", [stamp])
    assert stamp.replace(fold=1) == stamp
    assert not profile._check(case, "stdlib", [stamp.replace(fold=1)])
    day = clone_temporals._case("temporal-type", [stamp.date()])
    assert not profile._check(day, "stdlib", [stamp])


def test_snapshot_without_backend_disables_closed_temporals(tmp_path: Path) -> None:
    (tmp_path / "structural.py").write_text(
        "_native_clone = None\n_NATIVE_CLOSED_TEMPORALS = True\n"
    )
    snapshot = base._snapshot(tmp_path, "temporal_missing_backend")
    assert snapshot._NATIVE_CLOSED_TEMPORALS is False


@pytest.mark.parametrize("capability", [False, True])
def test_snapshot_inference_uses_its_own_temporal_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: bool
) -> None:
    @dataclasses.dataclass
    class Snapshot:
        _native_clone: Callable[[object], object]
        _NATIVE_CLOSED_TEMPORALS: bool

    def native(value: object) -> object:
        return value

    native.__module__ = "_clone_temporal_capability_backend"
    backend = ModuleType(native.__module__)
    backend.__dict__.update(clone_checked=native, CLOSED_TEMPORALS=capability)
    monkeypatch.setitem(sys.modules, native.__module__, backend)
    wrapper = Snapshot(native, not capability)

    def snapshot(_directory: Path, _label: str) -> Snapshot:
        return wrapper

    monkeypatch.setattr(base, "_snapshot", snapshot)
    (tmp_path / "structural.py").write_text(
        "from unstd.clone._clone_native import clone_checked as _native_clone\n"
    )
    actual = profile._snapshot(tmp_path, "temporal_backend", infer_native_bindings=True)
    assert actual._NATIVE_CLOSED_TEMPORALS is capability


def test_temporal_route_proof_uses_public_copy_and_restores_observers() -> None:
    if not getattr(structural, "_NATIVE_CLOSED_TEMPORALS", False):
        pytest.skip("native closed temporals are not built")
    graph = structural._native_graph
    previous = sys.getprofile()
    assert profile._temporal_route(structural, "current") == ["_native_graph"]
    assert structural._native_graph is graph
    assert sys.getprofile() is previous


@pytest.mark.parametrize("mode", ["python", "mixed"])
def test_temporal_route_rejects_hidden_continuations(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if not getattr(structural, "_NATIVE_CLOSED_TEMPORALS", False):
        pytest.skip("native closed temporals are not built")
    original = structural.deep
    mixed = structural._native_walk_graph
    assert mixed is not None

    def redundant(value: object) -> object:
        result = original(value)
        if mode == "python":
            structural._walk(value, {})
        else:
            mixed([], structural._walk, structural._record_admit, {})
        return result

    graph = structural._native_graph
    previous = sys.getprofile()
    monkeypatch.setattr(structural, "deep", redundant)
    with pytest.raises(RuntimeError, match="used mixed walking"):
        profile._temporal_route(structural, "redundant")
    assert structural._native_graph is graph
    assert sys.getprofile() is previous
