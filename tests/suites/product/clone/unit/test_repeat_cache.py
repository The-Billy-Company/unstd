"""Repeated children retain memo semantics across native graph and tree paths."""

import dataclasses
import gc
import sys

import pytest

from unstd import clone


native = pytest.importorskip("unstd.clone._clone_native")


@pytest.mark.parametrize("kind", [list, tuple])
def test_repeated_children_keep_graph_aliases_and_separate_asdict_positions(kind):
    shared = ["leaf", {"items": [1, 2]}]
    source = kind([0, shared, shared, 7, shared])
    actual = clone.deep(source)
    assert type(actual) is kind
    assert actual[1] is actual[2] is actual[4]
    assert actual[1] is not shared
    assert actual[1][1] is not shared[1]
    actual[1][1]["items"].append(3)
    assert actual[4][1]["items"] == [1, 2, 3]
    assert shared[1]["items"] == [1, 2]
    seeded = ["caller memo"]
    memo = {id(shared): seeded}
    seeded_actual = native.clone_graph(source, memo)
    assert seeded_actual is not NotImplemented
    assert seeded_actual[1] is seeded_actual[2] is seeded_actual[4] is seeded

    @dataclasses.dataclass
    class Row:
        values: object

    plain = clone.asdict(Row(source))["values"]
    assert type(plain) is kind
    assert plain[1] == plain[2] == plain[4] == shared
    assert plain[1] is not plain[2]
    assert plain[2] is not plain[4]
    assert plain[1][1]["items"] is not plain[2][1]["items"]


@pytest.mark.parametrize("kind", [list, tuple])
def test_foreign_memo_override_changes_later_repeated_children(kind):
    shared = [1]
    replacement = ["hook's memo entry"]

    class Hook:
        def __deepcopy__(self, memo):
            assert memo[id(shared)] == [1]
            memo[id(shared)] = replacement
            return "hook"

    source = kind([shared, Hook(), shared, shared])
    actual = clone.deep(source)
    assert actual[0] == [1]
    assert actual[0] is not shared
    assert actual[1] == "hook"
    assert actual[2] is actual[3] is replacement
    assert shared == [1]


@pytest.mark.parametrize("kind", [list, tuple])
def test_collector_mutation_of_completed_child_is_reused_by_later_aliases(kind):
    marker = "repeat-cache-audit-" + str(id(object()))
    shared = [marker]
    blocker = [17]
    source = kind([0, shared, shared, blocker, shared, shared])
    observed = False

    def mutate(phase, info):
        nonlocal observed
        if (
            phase != "start"
            or observed
            or getattr(sys, "_unstd_clone_fault_allocation", None) != 1
            or getattr(sys, "_unstd_clone_fault_source", None) != id(blocker)
        ):
            return
        observed = True
        copies = [
            value
            for value in gc.get_objects()
            if type(value) is list
            and value is not shared
            and len(value) == 1
            and value[0] is marker
        ]
        assert len(copies) == 1, len(copies)
        copied = copies[0]
        # A completed destination can be mutated through collector inspection.
        # Removing the source contents must not replace its established twin.
        shared.clear()
        copied.append(copied)

    gc.callbacks.append(mutate)
    try:
        actual = clone.deep(source)
    finally:
        gc.callbacks.remove(mutate)
    if not observed:
        pytest.skip("requires forced-GC allocation source markers")
    assert actual[1] is actual[2] is actual[4] is actual[5]
    assert actual[1][0] is marker
    assert actual[1][1] is actual[1]
    assert actual[3] == [17]
    assert actual[3] is not blocker
    assert shared == []
    del actual
    gc.collect()
    assert not any(
        type(value) is list
        and len(value) == 2
        and value[0] is marker
        and value[1] is value
        for value in gc.get_objects()
    ), "completed output cycle leaked"
