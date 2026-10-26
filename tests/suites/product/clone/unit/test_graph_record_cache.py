"""Warmed graph allocation caches retain protocol changes and weak ownership."""

from __future__ import annotations

import copy
import ctypes
import dataclasses
import gc
import sys
from typing import Any
import weakref

import pytest

from unstd.clone import structural


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyError])
def test_admission_cache_preserves_metaclass_mapping_callbacks(
    monkeypatch: pytest.MonkeyPatch, warm: bool, error_type: type[Exception]
) -> None:
    """Custom metaclasses retain both original weak mapping operations."""

    def observe(cached_pair: bool, fail_at: int | None):
        events = []
        armed = False
        sentinel = error_type("metaclass mapping callback")

        def record(event):
            if armed:
                events.append(event)
                if len(events) == fail_at:
                    raise sentinel

        class Meta(type):
            def __hash__(cls):
                record(("hash", cls.__name__))
                return 7  # Deliberate collisions exercise equality as well.

            def __eq__(cls, other):
                record(("eq", cls.__name__, other.__name__))
                return cls is other

        def make_row(name):
            return dataclasses.dataclass(
                Meta(name, (), {"__annotations__": {"values": object}})
            )

        other, row = make_row("Other"), make_row("Row")
        nodes = weakref.WeakKeyDictionary()
        records = weakref.WeakSet()
        monkeypatch.setattr(structural, "_NODES", weakref.WeakKeyDictionary())
        monkeypatch.setattr(structural, "_RECORDS", records)

        def original_admission(cls):
            # Independent original protocol: select once through a weak map,
            # then consult the weak set on every admitted dataclass lookup.
            try:
                node = nodes[cls]
            except KeyError:
                node = nodes[cls] = structural._admit(cls)
            return node is structural._dataclass and cls in records, (
                node or structural._foreign
            )

        admission = structural._record_admit if cached_pair else original_admission
        admission(other)
        if warm:
            admission(row)
        armed = True
        caught = None
        try:
            native, handler = admission(row)
        except error_type as error:
            caught = error
            result = "error"
        else:
            assert native is False
            assert handler is structural._dataclass
            result = "ok"
        if caught is not None:
            assert caught is sentinel
        return result, events

    expected = observe(False, None)
    assert observe(True, None) == expected
    for fail_at in range(1, len(expected[1]) + 1):
        assert observe(True, fail_at) == observe(False, fail_at)


@pytest.mark.parametrize("changed_protocol", ["allocator", "attributes"])
@pytest.mark.parametrize("version_churn", [0, 1100])
def test_warmed_graph_cache_delegates_after_class_protocol_changes(
    monkeypatch: pytest.MonkeyPatch, changed_protocol: str, version_churn: int
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

    # CPython 3.13/3.14 stop assigning a class version after 1000 changes.
    # Both stable cached versions and the exhausted uncached route must notice
    # the subsequent protocol change. Repeated calls warm every live version.
    for version in range(version_churn):
        Row.marker = version
        for _ in range(2):
            warm = native.clone_walk_graph(source, structural._walk, admit, {})
            assert warm == source
            assert warm.values is not source.values
    assert delegated == []
    if version_churn and (3, 13) <= sys.version_info < (3, 15):
        assign_version = ctypes.pythonapi.PyUnstable_Type_AssignVersionTag
        assign_version.argtypes = [ctypes.py_object]
        assign_version.restype = ctypes.c_int
        assert assign_version(Row) == 0

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
