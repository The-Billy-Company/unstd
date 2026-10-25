"""Common-case evidence checks must reject recreated atoms and missing nodes."""

from __future__ import annotations

import copy
from types import SimpleNamespace

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
