"""Warmed graph allocation caches retain protocol changes and weak ownership."""

from __future__ import annotations

import copy
import dataclasses
import gc
import sys
from typing import Any
import weakref

import pytest

from unstd.clone import structural


@pytest.mark.parametrize("changed_protocol", ["allocator", "attributes"])
def test_warmed_graph_cache_delegates_after_class_protocol_changes(
    monkeypatch: pytest.MonkeyPatch, changed_protocol: str
) -> None:
    native = pytest.importorskip("unstd.clone._clone_native")

    @dataclasses.dataclass
    class Row:
        values: list[int]

    source = Row([1, 2])
    delegated: list[object] = []

    def fallback(node: Any, memo: dict[int, Any]) -> Any:
        delegated.append(node)
        return copy.deepcopy(node, memo)

    def admit(_kind: type) -> tuple[bool, Any]:
        return True, fallback

    reference = weakref.ref(Row)
    references = sys.getrefcount(reference)
    for _ in range(4):
        warm = native.clone_walk_graph(source, structural._walk, admit, {})
        assert warm == source
        assert warm.values is not source.values
    assert delegated == []
    assert sys.getrefcount(reference) > references

    observations: list[object] = []
    if changed_protocol == "allocator":

        def allocate(kind: type[Row]) -> Row:
            observations.append("allocate")
            return object.__new__(kind)

        monkeypatch.setattr(Row, "__new__", staticmethod(allocate))
    else:

        def read(instance: Row, name: str) -> Any:
            observations.append(name)
            return object.__getattribute__(instance, name)

        monkeypatch.setattr(Row, "__getattribute__", read)

    expected = copy.deepcopy(source)
    expected_observations = observations.copy()
    observations.clear()
    actual = native.clone_walk_graph(source, structural._walk, admit, {})
    assert delegated == [source]
    assert observations == expected_observations
    assert type(actual) is Row
    assert actual == expected
    assert actual is not source
    assert actual.values is not source.values


def test_warmed_graph_cache_does_not_retain_generated_classes() -> None:
    native = pytest.importorskip("unstd.clone._clone_native")

    def generate() -> weakref.ReferenceType[type]:
        @dataclasses.dataclass
        class Ephemeral:
            values: list[int]

        source = Ephemeral([3])
        reference = weakref.ref(Ephemeral)
        references = sys.getrefcount(reference)
        for _ in range(4):
            warm = native.clone_walk_graph(
                source, structural._walk, structural._record_admit, {}
            )
            assert warm.values is not source.values
        assert sys.getrefcount(reference) > references
        return reference

    reference = generate()
    gc.collect()
    assert reference() is None
