"""Common-case evidence checks must reject recreated atoms and missing nodes."""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
from types import SimpleNamespace

import pytest

from bench.diagnostics.clone_cold import _clear_caches
from bench.profiles import clone_common_cpu as profile
from bench.profiles import clone_cpu as base
from unstd.clone import structural


def test_common_check_rejects_equal_but_recreated_atomic_leaves():
    atom = int("987654321987654321987654321")
    source = {"rows": [{"value": atom}]}
    result = {"rows": [{"value": int(str(atom))}]}
    case = profile._case("atoms", source)
    assert base._same(result, source)
    assert base._isolated(result, source)
    assert not profile._check(case, "current", result)
    assert profile._check(case, "stdlib", result)
    assert profile._check(case, "current", copy.deepcopy(source))


def test_dataclass_asdict_check_observes_atomic_identity():
    atom = int("987654321987654321987654321")
    source = base.Record("row", [atom])
    case = profile._case("record", source, "asdict")
    correct = {"name": source.name, "values": [atom]}
    recreated = {"name": source.name, "values": [int(str(atom))]}
    assert profile._check(case, "current", correct)
    assert not profile._check(case, "current", recreated)


def test_record_state_dictionary_reuse_fails_even_with_only_atomic_fields():
    source = base.Record("root", 7)
    result = object.__new__(base.Record)
    result.__dict__ = source.__dict__
    assert base._same(result, source)
    assert not base._isolated(result, source)
    assert not base._isolated(source.__dict__, source)


def test_node_counts_follow_each_operations_shared_reference_semantics():
    shared = [1, 2]
    source = [shared, shared]
    counts = profile._node_counts(source)
    assert counts == {"lists": 2, "dicts": 0, "tuples": 0, "records": 0}
    expanded = {**counts, "lists": 3}
    assert profile._node_counts(source, preserve_aliases=False) == expanded
    assert profile._node_counts(source, plain=True) == expanded


def test_node_counts_distinguish_dataclass_clone_from_plain_conversion():
    source = base.Record("root", [base.Record("row", [1, 2])])
    counts = profile._node_counts(source)
    assert counts == {"lists": 2, "dicts": 2, "tuples": 0, "records": 2}
    assert profile._node_counts(source, plain=True) == {**counts, "records": 0}


def test_cold_cache_control_clears_only_matching_admission_caches():
    module = SimpleNamespace(
        _NODES={"node": 1},
        _RECORDS={"record": 2},
        _FIELDS={"fields": 3},
        unrelated={"retained": 4},
    )
    assert _clear_caches(module) == ["_NODES", "_RECORDS", "_FIELDS"]
    assert not module._NODES
    assert not module._RECORDS
    assert not module._FIELDS
    assert module.unrelated == {"retained": 4}
    del module._RECORDS
    assert _clear_caches(module) == ["_NODES", "_FIELDS"]


def test_common_workloads_cover_distinct_shapes_and_protocol_controls():
    cases = profile._workloads()
    names = {case.name for case in cases}
    assert len(names) == len(cases)
    assert {
        "nested-small-5",
        "nested-200",
        "nested-wide-1000",
        "dictionary-of-records-500",
        "balanced-dict-list",
        "dataclass-small",
        "dataclasses-200",
        "dataclass-tree-200",
        "asdict-tree-200",
        "dataclasses-copy-hooks-40",
        "dataclasses-frozen-200",
        "dataclasses-slots-200",
        "dataclasses-flat-500",
        "mixed-keys-1000",
        "foreign-last",
    } <= names
    assert "cycle" not in names
    for case in cases:
        calls = base._callables(case, {"current": structural})
        assert all(profile._check(case, label, call()) for label, call in calls.items())


def test_installed_snapshot_inference_uses_only_its_wrappers_own_native_imports(
    tmp_path, monkeypatch
):
    (tmp_path / "structural.py").write_text(
        "from unstd.clone._clone_native import clone_checked as _native_clone\n"
        "from unstd.clone._clone_native import clone_graph as _native_graph\n"
    )

    def saved_clone(value):
        return value

    def saved_graph(value):
        return value

    saved_clone.__module__ = "_clone_common_saved_backend"
    backend = SimpleNamespace(clone_checked=saved_clone, clone_graph=saved_graph)
    monkeypatch.setitem(sys.modules, saved_clone.__module__, backend)
    wrapper = SimpleNamespace(
        _native_clone=saved_clone,
        _native_graph=None,
        _native_walk_graph=None,
        _NATIVE_SELECTED_MEMO=True,
    )
    monkeypatch.setattr(base, "_snapshot", lambda directory, label: wrapper)
    actual = profile._snapshot(tmp_path, "before", infer_native_bindings=True)
    assert actual._native_graph is saved_graph
    assert actual._native_walk_graph is None
    assert actual._NATIVE_SELECTED_MEMO is False


def test_snapshot_inference_preserves_an_explicit_manifest_override(
    tmp_path, monkeypatch
):
    (tmp_path / "structural.py").write_text(
        "from unstd.clone._clone_native import clone_graph as _native_graph\n"
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"native_graph_entrypoint": "reviewed_graph"})
    )

    def saved_clone(value):
        return value

    saved_clone.__module__ = "_clone_common_manifest_backend"
    monkeypatch.setitem(sys.modules, saved_clone.__module__, SimpleNamespace())
    selected = object()
    wrapper = SimpleNamespace(_native_clone=saved_clone, _native_graph=selected)
    monkeypatch.setattr(base, "_snapshot", lambda directory, label: wrapper)
    assert (
        profile._snapshot(tmp_path, "before", infer_native_bindings=True)._native_graph
        is selected
    )


def test_native_route_proofs_exercise_and_restore_public_bindings():
    pytest.importorskip("unstd.clone._clone_native")
    bindings = {
        name: getattr(structural, name, None) for name in profile._NATIVE_BINDINGS
    }
    routes = profile._native_routes(structural, "current")
    assert set(routes) == {"builtin-graph", "mixed-dataclass", "asdict-tree"}
    assert all(routes.values())
    assert bindings == {
        name: getattr(structural, name, None) for name in profile._NATIVE_BINDINGS
    }


def _historical_wrapper():
    native = pytest.importorskip("unstd.clone._clone_native")
    wrapper = SimpleNamespace(_native_clone=native.clone)
    wrapper.deep = lambda source: wrapper._native_clone(source)
    return wrapper


def test_historical_native_tree_proof_keeps_its_structural_alias_contract():
    historical = _historical_wrapper()
    original = historical._native_clone
    assert base._copy_contract(historical) == "structural"
    assert profile._native_routes(historical, "before", structural_baseline=True) == {
        "builtin-tree": ["_native_clone"]
    }
    assert historical._native_clone is original
    with pytest.raises(RuntimeError, match="before/builtin-graph"):
        profile._native_routes(historical, "before")


def test_historical_tree_proof_rejects_python_fallback_and_restores_bindings():
    historical = _historical_wrapper()
    original = historical._native_clone

    def python_tree(source):
        if isinstance(source, list):
            return [python_tree(value) for value in source]
        if isinstance(source, dict):
            return {key: python_tree(value) for key, value in source.items()}
        return source

    historical.deep = python_tree
    assert base._copy_contract(historical) == "structural"
    with pytest.raises(RuntimeError, match="before/builtin-tree"):
        profile._native_routes(historical, "before", structural_baseline=True)
    assert historical._native_clone is original


def test_historical_tree_proof_rejects_source_reuse_even_after_native_success():
    historical = _historical_wrapper()
    original = historical._native_clone

    def reused(source):
        result = historical._native_clone(source)
        if isinstance(source, dict):
            result["left"] = source["left"]
        return result

    historical.deep = reused
    with pytest.raises(RuntimeError, match="before/builtin-tree"):
        profile._native_routes(historical, "before", structural_baseline=True)
    assert historical._native_clone is original


def test_current_shared_graph_cannot_use_historical_tree_qualification():
    with pytest.raises(RuntimeError, match="historical structural copy contract"):
        profile._native_routes(structural, "current", structural_baseline=True)


@pytest.mark.parametrize("route", ["mixed-dataclass", "asdict-tree"])
def test_native_requirement_rejects_public_python_fallback(route, monkeypatch):
    pytest.importorskip("unstd.clone._clone_native")
    if route == "mixed-dataclass":
        for binding in (
            "_native_walk_graph",
            "_native_record_graph",
            "_native_dataclass",
        ):
            monkeypatch.setattr(structural, binding, None)
    else:
        monkeypatch.setattr(structural, "asdict", dataclasses.asdict)
    with pytest.raises(RuntimeError, match=f"before/{route}"):
        profile._native_routes(structural, "before")


def test_native_requirement_rejects_missing_graph_binding(monkeypatch):
    for binding in profile._NATIVE_BINDINGS:
        monkeypatch.setattr(structural, binding, None, raising=False)
    with pytest.raises(RuntimeError, match="before/builtin-graph"):
        profile._native_routes(structural, "before")


@pytest.mark.parametrize("slowdown", ["0.99", "nan", "inf", "-inf"])
def test_slowdown_option_rejects_invalid_thresholds(tmp_path, slowdown):
    with pytest.raises(SystemExit) as error:
        profile.main(["--compare-dir", str(tmp_path), f"--max-slowdown={slowdown}"])
    assert error.value.code == 2


@pytest.mark.parametrize("metric", ["median_wall_ns", "median_cpu_ns"])
def test_regression_gate_checks_each_metric_and_retains_evidence(
    tmp_path, monkeypatch, metric
):
    case = profile._case("checked-tree", {"values": [1, 2]})
    monkeypatch.setattr(profile, "_workloads", lambda: [case])
    monkeypatch.setattr(base, "_snapshot", lambda directory, label: structural)
    monkeypatch.setattr(base, "_metadata", lambda module: {"source": module.__file__})
    monkeypatch.setattr(base, "_profile", lambda call: [])
    timings = {
        label: {"median_wall_ns": 100.0, "median_cpu_ns": 100.0}
        for label in ("before", "current", "stdlib")
    }
    timings["current"][metric] = 131.0
    monkeypatch.setattr(base, "_timings", lambda calls, rounds, gc_enabled: timings)
    output = tmp_path / "evidence.json"
    with pytest.raises(SystemExit) as error:
        profile.main(
            [
                "--compare-dir",
                str(tmp_path),
                "--max-slowdown",
                "1.30",
                "--output",
                str(output),
            ]
        )
    assert error.value.code == 1
    saved = json.loads(output.read_text())
    assert saved["performance_gate"] == {
        "max_slowdown": 1.3,
        "comparison": "median of matched current/before batch ratios",
        "passed": False,
        "regressions": [
            {
                "workload": "checked-tree",
                "metric": metric,
                "before_ns": 100.0,
                "current_ns": 131.0,
                "slowdown": 1.31,
            }
        ],
    }
    assert saved["workloads"][case.name]["timings"]["before"][metric] == 100.0


def test_gate_accepts_an_exact_slowdown_limit():
    workloads = {
        "boundary": {
            "timings": {
                "before": {"median_wall_ns": 100.0, "median_cpu_ns": 100.0},
                "current": {"median_wall_ns": 130.0, "median_cpu_ns": 90.0},
            }
        }
    }
    assert profile._regressions(workloads, 1.3) == []


def _sampled_timing(samples):
    return {
        "median_wall_ns": sorted(samples)[len(samples) // 2],
        "median_cpu_ns": sorted(samples)[len(samples) // 2],
        "samples": [{"wall_ns": value, "cpu_ns": value} for value in samples],
    }


def test_common_worker_drift_cannot_create_a_clone_regression():
    current = _sampled_timing([100, 120, 120])
    before = _sampled_timing([100, 100, 120])
    assert current["median_cpu_ns"] / before["median_cpu_ns"] == 1.2
    workloads = {"drift": {"timings": {"current": current, "before": before}}}
    assert profile._regressions(workloads, 1.10) == []


def test_persistent_clone_slowdown_still_fails_during_worker_drift():
    current = _sampled_timing([120, 240, 360])
    before = _sampled_timing([100, 200, 300])
    workloads = {"slow": {"timings": {"current": current, "before": before}}}
    failures = profile._regressions(workloads, 1.10)
    assert {row["metric"] for row in failures} == {"median_wall_ns", "median_cpu_ns"}
    assert all(row["slowdown"] == 1.2 for row in failures)


@pytest.mark.parametrize(
    "samples", [[], [100], [100, 0, 100], [100, float("nan"), 100]]
)
def test_clone_guard_rejects_missing_or_invalid_paired_rounds(samples):
    before = _sampled_timing([100, 100, 100])
    current = _sampled_timing(samples) if samples else {"samples": []}
    with pytest.raises(ValueError, match=r"matched timing rounds|finite and positive"):
        profile._matched_slowdown(current, before, "median_cpu_ns")


def test_invalid_timing_pairs_save_evidence_before_failing(tmp_path, monkeypatch):
    case = profile._case("invalid-pair", [1, 2])
    monkeypatch.setattr(profile, "_workloads", lambda: [case])
    monkeypatch.setattr(base, "_snapshot", lambda directory, label: structural)
    monkeypatch.setattr(base, "_profile", lambda call: [])
    timings = {
        label: _sampled_timing([100, 100, 100])
        for label in ("current", "before", "stdlib")
    }
    timings["current"]["samples"] = []
    monkeypatch.setattr(base, "_timings", lambda calls, rounds, gc_enabled: timings)
    output = tmp_path / "invalid.json"
    with pytest.raises(SystemExit) as error:
        profile.main(
            [
                "--compare-dir",
                str(tmp_path),
                "--max-slowdown",
                "1.10",
                "--output",
                str(output),
            ]
        )
    assert error.value.code == 1
    saved = json.loads(output.read_text())
    assert "matched timing rounds" in saved["timing_error"]
    assert saved["workloads"][case.name]["timings"] == timings
