"""Mutation evidence preserves cardinality, validates models and isolates profiling."""

from __future__ import annotations

from argparse import Namespace
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


_spec = importlib.util.spec_from_file_location(
    "_containers_mutations_under_test",
    Path(__file__).resolve().parents[5] / "bench/profiles/containers_mutations.py",
)
assert _spec is not None
assert _spec.loader is not None
profile = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = profile
_spec.loader.exec_module(profile)


def _types():
    pytest.importorskip("sortedcontainers")
    pytest.importorskip("immutables")
    import unstd.containers as containers

    return containers


@pytest.mark.parametrize("size", [1, 128, 4096])
def test_all_mutation_models_and_return_contracts_run_without_clocks(size, monkeypatch):
    def forbidden():
        pytest.fail("correctness smoke must not touch clocks or collector state")

    monkeypatch.setattr(profile.core.time, "perf_counter_ns", forbidden)
    monkeypatch.setattr(profile.core.time, "process_time_ns", forbidden)
    monkeypatch.setattr(profile.core.gc, "disable", forbidden)
    monkeypatch.setattr(profile.core.gc, "enable", forbidden)
    cases = profile._cases(_types(), size, min(size, 64))
    assert len(cases) == 40
    assert len({case.name for case in cases}) == 40
    for case in cases:
        state = case.setup()
        original_length = len(state)
        result = case.run(state)
        case.check(state, result)
        if case.name.startswith(("steady.", "fallback.")):
            assert len(state) == original_length


@pytest.mark.parametrize(
    "name",
    [
        "steady.sorted_list.random_replace.indexed",
        "steady.sorted_list.rolling_expiration.unindexed",
        "steady.sorted_dict.random_replace.indexed",
        "fallback.keyed_records.random_replace",
        "batch.sorted_list.update",
        "batch.sorted_set.difference_update",
        "isolated.sorted_list.remove.indexed",
        "isolated.sorted_list.delitem.indexed",
        "slice.sorted_list.prefix.fraction.indexed",
        "append.sorted_list.update_tuple.indexed",
    ],
)
def test_corrupt_results_fail_the_independent_state_or_return_oracle(name):
    case = next(case for case in profile._cases(_types(), 128, 32) if case.name == name)
    state = case.setup()
    with pytest.raises(AssertionError, match="oracle mismatch"):
        case.check(state, object())


def test_indexed_variants_build_the_index_before_mutation():
    cases = profile._cases(_types(), 4096, 32)
    for case in cases:
        if case.name.startswith("steady.") and case.name.endswith(".indexed"):
            state = case.setup()
            ordered = (
                state if case.name.startswith("steady.sorted_list.") else state._list
            )
            assert ordered._index


@pytest.mark.parametrize("size", [65536, 1000000])
def test_large_slice_windows_cover_safe_and_fallback_boundaries_below_rebuild_threshold(
    size,
):
    windows = {
        name: (first, last) for name, first, last in profile._slice_windows(size)
    }
    assert len(windows) == 8
    assert windows["prefix.fraction"] == (0, size // 10)
    assert windows["suffix.fraction"] == (size - size // 10, size)
    assert windows["prefix.aligned"][1] % 1000 == 0
    assert windows["suffix.aligned"][0] % 1000 == 0
    assert 1000 - windows["prefix.partial_kept_large"][1] % 1000 > 500
    assert 1000 - windows["prefix.partial_underflow"][1] % 1000 < 500
    assert windows["suffix.partial_kept_large"][0] % 1000 > 500
    assert windows["suffix.partial_underflow"][0] % 1000 < 500
    assert all(
        last - first > 0 and (last - first) * 8 < size
        for first, last in windows.values()
    )


def test_large_slice_models_include_indexed_and_unindexed_identity_preserving_deletes():
    cases = profile._cases(_types(), 65536, 64)
    assert len(cases) == 52
    slices = [case for case in cases if case.name.startswith("slice.")]
    assert len(slices) == 20
    for case in slices:
        state = case.setup()
        result = case.run(state)
        case.check(state, result)


def test_append_cases_use_one_existing_update_call_and_retain_input_and_value_identities():
    cases = [
        case
        for case in profile._cases(_types(), 4096, 32)
        if case.name.startswith("append.")
    ]
    assert len(cases) == 4
    assert all(case.operations == 409 for case in cases)
    for case in cases:
        state = case.setup()
        before = list(state)
        update = state.update
        observed = []

        def counted(payload, update=update, observed=observed):
            observed.append(payload)
            return update(payload)

        state.update = counted
        result = case.run(state)
        case.check(state, result)
        assert len(observed) == 1
        assert list(state)[: len(before)] == before
        assert len(state) == len(before) + 409


def test_append_oracle_rejects_equal_values_with_replaced_identities():
    types = _types()
    case = next(
        case
        for case in profile._cases(types, 4096, 32)
        if case.name == "append.sorted_list.update_list.unindexed"
    )
    state = case.setup()
    result = case.run(state)
    equal_copy = types.SortedList(int(str(value)) for value in state)
    with pytest.raises(AssertionError, match="retained value's identity"):
        case.check(equal_copy, result)


def test_append_oracle_rejects_mutation_of_the_retained_input_list():
    case = next(
        case
        for case in profile._cases(_types(), 4096, 32)
        if case.name == "append.sorted_list.update_list.unindexed"
    )
    state = case.setup()
    update = state.update

    def corrupt(payload):
        result = update(payload)
        payload.reverse()
        return result

    state.update = corrupt
    result = case.run(state)
    with pytest.raises(AssertionError, match="oracle mismatch"):
        case.check(state, result)


def test_slice_oracle_rejects_equal_values_with_replaced_identities():
    types = _types()
    case = next(
        case
        for case in profile._cases(types, 4096, 64)
        if case.name == "slice.sorted_list.prefix.fraction.unindexed"
    )
    state = case.setup()
    result = case.run(state)
    equal_copy = types.SortedList(int(str(value)) for value in state)
    with pytest.raises(AssertionError, match="retained value's identity"):
        case.check(equal_copy, result)


@pytest.mark.parametrize("direction", ["prefix", "suffix"])
def test_exclusive_id_victims_have_no_fixture_owner_and_retained_identities_survive(
    direction,
):
    if sys.implementation.name != "cpython":
        pytest.skip("ownership observation uses CPython reference counts")
    case = next(
        case
        for case in profile._cases(_types(), 4096, 64)
        if case.name == f"slice.sorted_list.{direction}.exclusive_ids.unindexed"
    )
    state = case.setup()
    victim = state._list[1 if direction == "prefix" else len(state) - 2]
    # Relative to a fresh local-only integer, exactly one container reference.
    # The selected ID is not a maximum; this also handles borrowed call arguments.
    control = int(str(victim))
    assert control is not victim
    assert sys.getrefcount(victim) == sys.getrefcount(control) + 1
    del victim
    result = case.run(state)
    case.check(state, result)
    assert len(state.retained) == 4096 - 409


def test_structural_route_proof_keeps_the_actual_population():
    args = Namespace(
        size=1000000, batch=2048, iterations=8, include=["slice."], smoke=False
    )
    command = profile._command(args, Path("/saved/source"), proof=True)
    assert command[command.index("--size") + 1] == "1000000"
    assert command[command.index("--iterations") + 1] == "1"
    assert "--proof" in command
    assert "--smoke" in command


def test_route_profile_is_separate_and_retains_call_counts_without_profile_times(
    monkeypatch,
):
    def forbidden():
        pytest.fail("route proof must not call the measurement clocks")

    monkeypatch.setattr(profile.core.time, "perf_counter_ns", forbidden)
    monkeypatch.setattr(profile.core.time, "process_time_ns", forbidden)
    case = next(
        case
        for case in profile._cases(_types(), 128, 16)
        if case.name == "steady.sorted_list.random_replace.unindexed"
    )
    proof = profile._route_proof(case)
    calls = proof["calls"]
    assert calls
    assert any("add" in row["function"] for row in calls)
    assert any("remove" in row["function"] for row in calls)
    assert all(set(row) == {"function", "calls", "builtin"} for row in calls)
    assert all(row["calls"] > 0 for row in calls)


def test_route_counters_reset_after_setup_and_snapshot_only_the_profiled_call(
    monkeypatch,
):
    events = []

    class OwnList(list):
        _unstd_sorted_native_config = object()

    def stats(cls, reset=False):
        assert cls is OwnList
        events.append("reset" if reset else "snapshot")
        return {
            "delete_fast": int(not reset),
            "delete_fallback": 0,
            "update_fast": 0,
            "update_fallback": 0,
        }

    def setup():
        events.append("setup")
        return OwnList([1, 2])

    def run(state):
        events.append("run")
        del state[:1]

    def check(state, result):
        events.append("check")
        assert state == [2]
        assert result is None

    monkeypatch.setitem(
        profile.sys.modules,
        "unstd.containers._sorted_native",
        SimpleNamespace(mutation_stats=stats),
    )
    proof = profile._route_proof(
        profile.core.Case("slice.fixture", setup, run, check, 1)
    )
    assert events == ["setup", "reset", "run", "snapshot", "check"]
    assert proof["mutation_stats"]["delete_fast"] == 1


def test_old_baseline_without_stats_api_reports_null(monkeypatch):
    monkeypatch.delitem(
        profile.sys.modules, "unstd.containers._sorted_native", raising=False
    )
    case = profile.core.Case(
        "fixture", lambda: [], lambda _state: None, lambda _state, _result: None, 1
    )
    assert profile._route_proof(case)["mutation_stats"] is None


def test_worker_caps_large_population_iterations_and_bounds_controls(monkeypatch):
    containers = _types()
    source = Path(containers.__file__).resolve().parents[2]
    case = profile.core.Case(
        "fixture", lambda: None, lambda _state: None, lambda _state, _result: None, 1
    )
    observed = []
    monkeypatch.setattr(profile, "_cases", lambda *_args: [case])
    monkeypatch.setattr(
        profile.core,
        "_sample",
        lambda _case, iterations, *, smoke: observed.append((iterations, smoke)) or {},
    )
    monkeypatch.setattr(
        profile.core, "_worker", lambda *_args, **_kwargs: {"environment": {}}
    )
    args = Namespace(
        source=source,
        size=1000000,
        batch=2048,
        iterations=32,
        include=None,
        smoke=True,
        proof=False,
    )
    result = profile._worker(args)
    assert observed == [(1, True)]
    assert result["fallback_population"] == 4096
    assert result["population"] == 1000000


def test_paired_sources_alternate_and_algorithm_ratios_remain_separate(
    tmp_path, monkeypatch
):
    observed = []
    caches = []

    def worker(command, source, cache, *, warm=False):
        label = source.name
        observed.append((label, warm))
        caches.append(cache)
        assert "--proof" in command if warm else "--proof" not in command
        assert "--smoke" in command if warm else "--smoke" not in command
        steady = 100 if label == "before" else 50
        repeated = 100 if label == "before" else 80
        times = {
            "steady.fixture": steady,
            "batch.sorted_list.repeated_add": repeated,
            "batch.sorted_list.update": 20,
        }
        return {
            "environment": {"same": True},
            "provenance": {"source": str(source)},
            "dispatch": {},
            "cases": {
                name: {"wall_ns_per_operation": value, "cpu_ns_per_operation": value}
                for name, value in times.items()
            },
        }

    monkeypatch.setattr(profile.core, "_run", worker)
    args = Namespace(
        source=tmp_path / "current",
        compare_source=tmp_path / "before",
        size=4096,
        batch=128,
        iterations=8,
        rounds=3,
        include=None,
        smoke=False,
        output=tmp_path / "report.json",
        require_native_mutations=False,
    )
    report = profile._compare(args)
    assert observed == [
        ("before", True),
        ("current", True),
        ("before", False),
        ("current", False),
        ("current", False),
        ("before", False),
        ("before", False),
        ("current", False),
    ]
    assert len(set(caches)) == 1
    assert (
        report["before_current_comparisons"]["steady.fixture"]["wall_ns_per_operation"][
            "before_over_current"
        ]
        == 2
    )
    assert (
        report["before_current_comparisons"]["batch.sorted_list.update"][
            "wall_ns_per_operation"
        ]["before_over_current"]
        == 1
    )
    assert report["existing_api_algorithm_comparisons"]["batch.sorted_list.update"] == {
        "before": 5,
        "current": 4,
    }
    assert len(report["proof_runs"]) == 2
    assert len(report["runs"]) == 6
    assert args.output.is_file()


@pytest.mark.parametrize(
    ("descriptor", "accepted"),
    [
        ("_loc", False),
        ("_pos", False),
        ("add", True),
        ("_expand", True),
        ("_delete", True),
        ("__delitem__", True),
        ("update", True),
    ],
)
def test_native_mutation_requirement_cannot_pass_on_read_kernels_only(
    tmp_path, monkeypatch, descriptor, accepted
):
    def worker(_command, source, _cache, *, warm=False):
        return {
            "environment": {},
            "provenance": {"source": str(source)},
            "dispatch": {"mutation_descriptors": {"SortedList": {descriptor: "owner"}}},
            "cases": {"slice.fixture": {}},
            "route_proof": {"slice.fixture": {"mutation_stats": {"delete_fast": 1}}},
        }

    monkeypatch.setattr(profile.core, "_run", worker)
    args = Namespace(
        source=tmp_path / "current",
        compare_source=tmp_path / "before",
        size=128,
        batch=16,
        iterations=1,
        rounds=1,
        include=None,
        smoke=True,
        output=None,
        require_native_mutations=True,
    )
    if accepted:
        assert profile._compare(args)["complete"]
    else:
        with pytest.raises(RuntimeError, match="native mutation descriptors"):
            profile._compare(args)


def test_mutation_cli_rejects_missing_baseline_and_invalid_timing():
    with pytest.raises(SystemExit):
        profile._arguments([])
    with pytest.raises(SystemExit):
        profile._arguments(["--size", "0"])
    with pytest.raises(SystemExit):
        profile._arguments(
            ["--compare-source", str(profile._ROOT / "src"), "--rounds", "1"]
        )


@pytest.mark.parametrize(
    "stats", [None, {"delete_fast": 0, "update_fast": 0, "update_fallback": 1}]
)
def test_native_descriptor_without_actual_fast_counter_is_rejected(
    tmp_path, monkeypatch, stats
):
    def worker(_command, source, _cache, *, warm=False):
        return {
            "environment": {},
            "provenance": {"source": str(source)},
            "dispatch": {"mutation_descriptors": {"SortedList": {"update": "owner"}}},
            "cases": {"append.fixture": {}},
            "route_proof": {"append.fixture": {"mutation_stats": stats}},
        }

    monkeypatch.setattr(profile.core, "_run", worker)
    args = Namespace(
        source=tmp_path / "current",
        compare_source=tmp_path / "before",
        size=128,
        batch=16,
        iterations=1,
        rounds=1,
        include=None,
        smoke=True,
        output=None,
        require_native_mutations=True,
    )
    with pytest.raises(RuntimeError, match="actual bulk mutation fast path"):
        profile._compare(args)


def test_fallback_control_must_report_zero_fast_counters(tmp_path, monkeypatch):
    def worker(_command, source, _cache, *, warm=False):
        return {
            "environment": {},
            "provenance": {"source": str(source)},
            "dispatch": {},
            "cases": {"fallback.fixture": {}},
            "route_proof": {"fallback.fixture": {"mutation_stats": {"delete_fast": 1}}},
        }

    monkeypatch.setattr(profile.core, "_run", worker)
    args = Namespace(
        source=tmp_path / "current",
        compare_source=tmp_path / "before",
        size=128,
        batch=16,
        iterations=1,
        rounds=1,
        include=None,
        smoke=True,
        output=None,
        require_native_mutations=False,
    )
    with pytest.raises(RuntimeError, match="fallback control unexpectedly"):
        profile._compare(args)


def test_direct_worker_selects_a_partial_saved_package_before_any_product_import(
    tmp_path,
):
    _types()
    source = tmp_path / "saved"
    package = source / "unstd/containers"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text('"""Isolated saved product."""\n')
    (package / "__init__.py").write_text(
        "from sortedcontainers import SortedList, SortedDict, SortedSet\nfrom immutables import Map\n"
    )
    args = Namespace(
        source=source,
        size=128,
        batch=16,
        iterations=1,
        include=["steady.sorted_list.random_replace.unindexed"],
        smoke=True,
    )
    result = profile.core._run(
        profile._command(args, source), source, tmp_path / "cache"
    )
    assert result["provenance"]["imported"] == str(package / "__init__.py")
    assert set(result["cases"]) == {"steady.sorted_list.random_replace.unindexed"}
