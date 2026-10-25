"""Native performance evidence must prove dispatch and reject changed contracts."""

from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest

from bench.core.timing import Measurement
from bench.profiles import native_profile
from bench.profiles.native_profile import (
    Record,
    Workload,
    _assert_native_execution,
    _base_text,
    _bundle,
    _isolated,
    _load,
    _native_modules,
    _routes,
    _same,
    _timings,
    _validate_comparison,
    _workloads,
)
from tests.support.environment.anchors import REPO_ROOT
from unstd.clone import structural
from unstd.text import fuzz


@pytest.mark.parametrize(
    "value", [[1], {"nested": [1]}, [{"nested": [2]}], Record("row", [1])]
)
def test_mutable_identity_and_shallow_clones_are_rejected(value: object) -> None:
    assert _same(value, value)
    assert not _isolated(value, value)
    assert _isolated(copy.deepcopy(value), value)
    assert not _isolated({"x": value}, {"x": value})


def test_value_check_observes_exact_types_and_dictionary_order() -> None:
    assert not _same({True: [1]}, {1: [1]})
    assert not _same({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not _same([1, 2], (1, 2))


def test_workloads_cover_boundaries_fallbacks_and_compatibility() -> None:
    text = SimpleNamespace(**vars(fuzz))
    _base_text(text)
    cases = _workloads(structural, text)
    assert all(case.check(case.call()) for case in cases)
    names = {case.name for case in cases}
    assert {f"text.distance/unicode{n}" for n in (12, 64, 65, 256, 1024)} <= names
    assert {
        "clone.deep/small",
        "clone.deep/records200",
        "clone.deep/records500",
        "clone.deep/mixed-custom-fallback",
        "clone.deep/dataclass-root",
        "clone.deep/dataclass-records",
        "clone.deep/dataclass-wrapped",
        "clone.deep/shared-branch-semantics",
        "clone.deep/cycle-fallback",
        "text.distance/sequence-fallback",
        "text.distance/subclass-fallback",
        "text.jaro_winkler/short-name",
        "text.jaro_winkler/unicode75",
        "text.jaro_winkler/subclass-fallback",
    } <= names
    shared = next(case for case in cases if "shared-branch" in case.name)
    result = shared.call()
    result[1] = copy.deepcopy(result[0])
    assert not shared.check(result)


def test_base_selection_preserves_python_wrappers_and_records_prior_selection() -> None:
    text = SimpleNamespace(**vars(fuzz))
    selected = _base_text(text)
    assert set(selected) == {"distance", "jaro_winkler"}
    assert text.distance is fuzz._levenshtein
    assert text.jaro_winkler is fuzz._jaro_winkler
    assert text._HAVE_RAPIDFUZZ is False


@pytest.mark.parametrize("walk", ["clone_walk_graph", "clone_walk_canonical_graph"])
def test_native_proof_requires_each_real_extension_profile_entry(walk: str) -> None:
    names = [
        "unstd.clone._clone_native.clone_graph",
        f"unstd.clone._clone_native.{walk}",
        "unstd.text._text_native.distance",
        "unstd.text._text_native.jaro",
    ]
    entries = [{"function": f"<built-in method {name}>", "calls": 3} for name in names]
    assert len(_assert_native_execution({"c": entries}, walk_entrypoint=walk)) == 4
    other = (
        "clone_walk_canonical_graph"
        if walk == "clone_walk_graph"
        else "clone_walk_graph"
    )
    with pytest.raises(AssertionError, match="kernel did not execute"):
        _assert_native_execution({"c": entries}, walk_entrypoint=other)
    with pytest.raises(AssertionError, match="kernel did not execute"):
        _assert_native_execution({"c": entries[:-1]}, walk_entrypoint=walk)
    with pytest.raises(AssertionError, match="kernel did not execute"):
        _assert_native_execution(
            {"c": [{"function": "<built-in method other.jaro>", "calls": 3}]},
            walk_entrypoint=walk,
        )
    legacy = [
        {**row, "function": row["function"].replace("clone_graph>", "clone>")}
        for row in entries
    ]
    with pytest.raises(AssertionError, match=r"kernel did not execute: .+clone_graph"):
        _assert_native_execution({"c": legacy}, walk_entrypoint=walk)


def test_clone_walk_proof_rejects_python_wrappers_and_other_builtins() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    selected = SimpleNamespace(_native_walk_graph=native.clone_walk_graph)
    assert native_profile._walk_entrypoint(selected) == "clone_walk_graph"
    selected._native_walk_graph = native.clone_graph
    assert native_profile._walk_entrypoint(selected) == ""

    def clone_walk_graph(*args):
        return native.clone_walk_graph(*args)

    clone_walk_graph.__module__ = native.__name__
    selected._native_walk_graph = clone_walk_graph
    assert native_profile._walk_entrypoint(selected) == ""


def test_native_module_absence_is_explicit_failure(monkeypatch) -> None:
    def missing(name: str) -> None:
        raise ImportError(name)

    monkeypatch.setattr(native_profile.importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="required native module is unavailable"):
        _native_modules(REPO_ROOT / "src")


def test_missing_comparison_source_is_explicit_failure(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="missing comparison source"):
        _load(tmp_path, "text/fuzz.py", "_missing_native_profile_source")


def test_signature_changes_abort_comparison_before_timing() -> None:
    before = {"signatures": {"clone.deep": "(obj)"}, "cases": []}
    after = {"signatures": {"clone.deep": "(obj, *, changed=False)"}, "cases": []}
    with pytest.raises(AssertionError, match="public signatures changed"):
        _validate_comparison({"before": before, "after": after})


@pytest.mark.parametrize("key", ["installed_signatures", "installed_text_selection"])
def test_normalized_base_wrappers_cannot_hide_installed_api_changes(key) -> None:
    before = {"signatures": {}, "cases": [], key: {"distance": "original"}}
    after = {"signatures": {}, "cases": [], key: {"distance": "changed"}}
    with pytest.raises(AssertionError, match=r"installed .+ changed"):
        _validate_comparison({"before": before, "after": after})


def test_comparison_rejects_self_consistent_changed_output() -> None:
    before = {
        "signatures": {},
        "cases": [Workload("case", lambda: 1, lambda result: result == 1)],
    }
    after = {
        "signatures": {},
        "cases": [Workload("case", lambda: 2, lambda result: result == 2)],
    }
    with pytest.raises(AssertionError, match="comparison output mismatch"):
        _validate_comparison({"before": before, "after": after})


def test_saved_alias_expansion_and_current_identity_are_checked_separately() -> None:
    legacy = SimpleNamespace(**vars(structural))
    legacy._walk = lambda source: source
    legacy.deep = lambda source: [copy.deepcopy(item) for item in source]
    text = SimpleNamespace(**vars(fuzz))
    _base_text(text)
    cases = [
        next(
            case
            for case in _workloads(module, text)
            if case.name == "clone.deep/shared-branch-semantics"
        )
        for module in (legacy, structural)
    ]
    old, new = cases
    assert old.check(old.call())
    assert new.check(new.call())
    assert not old.check(new.call())
    assert not new.check(old.call())
    _validate_comparison(
        {
            "before": {"signatures": {}, "cases": [old]},
            "after": {"signatures": {}, "cases": [new]},
        }
    )


def test_timing_alternates_order_and_keeps_measurements_separate(monkeypatch) -> None:
    calls = []

    def measured(call, *, rounds):
        calls.append(call())
        return Measurement(per_op_ns=10, iters=5, rounds=rounds, spread=0.1)

    monkeypatch.setattr(native_profile, "measure", measured)
    bundles = {
        label: {
            "cases": [Workload("case", lambda value=label: value, lambda result: True)]
        }
        for label in ("before", "after")
    }
    result = _timings(bundles, rounds=2, repetitions=3)
    assert calls == ["before", "after", "after", "before", "before", "after"]
    assert len(result["case"]["samples"]["before"]) == 3
    assert result["case"]["samples"]["after"][0]["rounds"] == 2


def test_built_extensions_are_exercised_by_the_public_workloads() -> None:
    pytest.importorskip("unstd.clone._clone_native")
    pytest.importorskip("unstd.text._text_native")
    result = _bundle(REPO_ROOT / "src", "gate_native", require_native=True)
    assert len(result["native_modules"]) == 2
    for name, metadata in result["native_modules"].items():
        source = REPO_ROOT / "native" / name.split(".")[1] / "src/adapter.c"
        assert (
            metadata["c_source_sha256"]
            == hashlib.sha256(source.read_bytes()).hexdigest()
        )
    assert len(result["native_execution"]) == 4
    assert result["profile"]["python"]
    assert result["profile"]["c"]
    assert result["installed_signatures"] == native_profile._signatures(
        structural, fuzz
    )
    assert len(result["routes"]) == len(result["cases"])
    assert result["routes"]["clone.deep/small"]["native_returns"] == {
        "graph": [True, True, True]
    }
    assert result["routes"]["clone.deep/mixed-custom-fallback"]["native_returns"] == {
        "graph": [False, False, False],
        "walk": [True, True, True],
    }
    cycle = result["routes"]["clone.deep/cycle-fallback"]
    assert cycle["native_returns"] == {}
    assert cycle["native_recursion_errors"] == {"graph": 3}
    assert cycle["python_calls"]["deepcopy"]
    assert "_walk" not in cycle["python_calls"]
    records = result["routes"]["clone.deep/dataclass-records"]
    assert records["native_returns"]["walk"] == [True] * 3
    assert records["native_returns"]["graph"] == [False, False, False]
    assert "_walk" not in records["python_calls"]


def test_clone_routes_are_proven_without_the_optional_text_extension() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    expected = getattr(native, "clone_walk_canonical_graph", native.clone_walk_graph)
    assert structural._native_walk_graph is expected
    assert native_profile._walk_entrypoint(structural) == expected.__name__
    text = SimpleNamespace(**vars(fuzz))
    _base_text(text)
    cases = [
        case
        for case in _workloads(structural, text)
        if case.name.startswith("clone.deep/")
    ]
    routes = _routes(cases, REPO_ROOT / "src", structural, text)
    for name in ("dataclass-root", "dataclass-records", "dataclass-wrapped"):
        route = routes[f"clone.deep/{name}"]
        assert route["native_returns"]["walk"] == [True] * 3
        assert "_walk" not in route["python_calls"]


def test_cycle_route_rejects_python_walking_before_deepcopy(monkeypatch) -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    pytest.importorskip("unstd.text._text_native")
    source = REPO_ROOT / "src"
    clone = _load(source, "clone/structural.py", "_route_cycle_clone")
    text = _load(source, "text/fuzz.py", "_route_cycle_text")
    _base_text(text)
    # The legacy entry point refuses cycles, forcing the recursive Python walk.
    # Equal output cannot qualify that slower route as immediate cycle fallback.
    monkeypatch.setattr(clone, "_native_graph", lambda source: native.clone(source))
    monkeypatch.setattr(clone, "_native_walk_graph", None)
    cases = [
        case
        for case in _workloads(clone, text)
        if case.name == "clone.deep/cycle-fallback"
    ]
    assert len(cases) == 1
    assert cases[0].check(cases[0].call())
    with pytest.raises(AssertionError, match="unexpected native/fallback route"):
        _routes(cases, source, clone, text)
    assert clone._native_clone is native.clone_checked


@pytest.mark.parametrize(
    ("module_name", "attribute", "workload", "unsupported"),
    [
        ("clone", "_native_graph", "clone.deep/small", (object(),)),
        (
            "clone",
            "_native_walk_graph",
            "clone.deep/dataclass-records",
            (object(), structural._walk, structural._record_admit, {}),
        ),
        (
            "text",
            "_native_distance",
            "text.distance/unicode64",
            ([], []),
        ),
        (
            "text",
            "_native_jaro",
            "text.jaro_winkler/short-name",
            ([], []),
        ),
    ],
)
def test_native_call_that_refuses_input_cannot_prove_acceleration(
    module_name, attribute, workload, unsupported, monkeypatch
) -> None:
    pytest.importorskip("unstd.clone._clone_native")
    pytest.importorskip("unstd.text._text_native")
    source = REPO_ROOT / "src"
    clone = _load(source, "clone/structural.py", "_route_refusal_clone")
    text = _load(source, "text/fuzz.py", "_route_refusal_text")
    _base_text(text)
    module = clone if module_name == "clone" else text
    native = getattr(module, attribute)

    def refused(*args):
        # The real builtin executes, but the public wrapper must do the work.
        native(*unsupported)
        return NotImplemented

    monkeypatch.setattr(module, attribute, refused)
    cases = [case for case in _workloads(clone, text) if case.name == workload]
    assert len(cases) == 1
    assert cases[0].check(cases[0].call())
    with pytest.raises(AssertionError, match="unexpected native/fallback route"):
        _routes(cases, source, clone, text)
    # Failed proofs must not leave instrumentation installed on the module.
    assert getattr(module, attribute) is refused


def test_current_native_calls_cannot_contaminate_python_comparison_baseline() -> None:
    pytest.importorskip("unstd.clone._clone_native")
    pytest.importorskip("unstd.text._text_native")
    with pytest.raises(AssertionError, match="Python-only baseline"):
        _bundle(REPO_ROOT / "src", "contaminated_before", require_native=False)


@pytest.mark.parametrize("kernel", ["graph", "dataclass", "walk"])
def test_secondary_clone_kernel_cannot_hide_in_a_python_only_baseline(
    monkeypatch,
    kernel,
) -> None:
    native = pytest.importorskip("unstd.clone._clone_native")
    load = native_profile._load

    def without_other_kernels(source, relative, name):
        module = load(source, relative, name)
        if relative == "clone/structural.py":
            module._native_clone = lambda source: NotImplemented
            module._native_records = None
            module._native_record_graph = None
            module._native_walk_graph = (
                native.clone_walk_graph if kernel == "walk" else None
            )
            module._native_graph = native.clone_graph if kernel == "graph" else None
            module._native_dataclass = (
                native.clone_dataclass if kernel == "dataclass" else None
            )
        else:
            module._native_distance = None
            module._native_jaro = None
        return module

    monkeypatch.setattr(native_profile, "_load", without_other_kernels)
    with pytest.raises(AssertionError, match="Python-only baseline"):
        _bundle(REPO_ROOT / "src", "record_contaminated_before", require_native=False)
