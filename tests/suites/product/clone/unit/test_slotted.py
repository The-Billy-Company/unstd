"""Slot records keep the default reducer's state, identity and restoration order."""

from __future__ import annotations

import copy
import copyreg
import dataclasses
from typing import ClassVar, Self, SupportsIndex, override

import pytest

from unstd import clone
from unstd.clone import structural


@pytest.fixture(params=("python", "native"))
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "native":
        pytest.importorskip("unstd.clone._clone_native")
    else:
        for name in vars(structural):
            if name.startswith("_native_"):
                monkeypatch.setattr(structural, name, None)


@dataclasses.dataclass(slots=True)
class Row:
    first: object
    second: object
    seed: dataclasses.InitVar[int] = 3
    derived: int = dataclasses.field(init=False)
    initializations: ClassVar[int] = 0

    def __post_init__(self, seed: int) -> None:
        type(self).initializations += 1
        self.derived = seed * 2


@dataclasses.dataclass(slots=True, frozen=True)
class Frozen:
    first: object
    second: object


@dataclasses.dataclass(slots=True)
class Child(Row):
    tail: object = None


@dataclasses.dataclass
class DictionaryChild(Row):
    tail: object = None


@pytest.mark.parametrize("record", [Row, Frozen, Child, DictionaryChild])
def test_slots_retain_inherited_state_aliases_and_skip_initialization(
    backend: None, record: type[Row | Frozen | Child | DictionaryChild]
) -> None:
    shared = [{"values": [1, 2]}]
    source = record(shared, shared)
    initializations = getattr(record, "initializations", 0)
    expected = copy.deepcopy(source)
    first, second = clone.deep(source), clone.deep(source)
    assert first == expected == second
    assert type(first) is record
    assert first.first is first.second
    assert first.first is not shared
    assert first.first is not second.first
    assert getattr(record, "initializations", 0) == initializations
    if isinstance(first, Row):
        assert isinstance(source, Row)
        assert first.derived == source.derived == 6


def test_slot_and_dictionary_positions_share_one_memo(backend: None) -> None:
    shared = [1, 2]
    source = DictionaryChild(shared, shared, tail=shared)
    source.__dict__["extra"] = shared
    actual = clone.deep(source)
    assert actual.first is actual.second is actual.tail is actual.__dict__["extra"]
    assert actual.first is not shared
    assert actual == copy.deepcopy(source)


@pytest.mark.parametrize("protocol", ["__deepcopy__", "__reduce_ex__"])
def test_dictionary_bearing_slot_subclasses_keep_instance_protocols(
    backend: None, protocol: str
) -> None:
    source = DictionaryChild([1], [2])
    if protocol == "__deepcopy__":
        source.__dict__[protocol] = lambda _memo: ["private hook"]
    else:
        source.__dict__[protocol] = lambda _version: (list, (["private hook"],))
    actual: object = clone.deep(source)
    expected: object = copy.deepcopy(source)
    assert actual == expected == ["private hook"]


def test_slot_getattr_keeps_protocol_lookup_observations(backend: None) -> None:
    calls: list[str] = []

    @dataclasses.dataclass(slots=True)
    class Dynamic:
        values: list[int]

        def __getattr__(self, name: str) -> object:
            calls.append(name)
            raise AttributeError(name)

    source = Dynamic([1])
    reference = copy.deepcopy(source)
    expected_calls = list(calls)
    calls.clear()
    actual = clone.deep(source)
    assert actual == reference
    assert calls == expected_calls


def test_uninitialized_mutable_slots_remain_uninitialized(backend: None) -> None:
    source = Row([1], [2])
    del source.first
    expected, actual = copy.deepcopy(source), clone.deep(source)
    assert not hasattr(expected, "first")
    assert not hasattr(actual, "first")
    assert actual.second == expected.second == [2]
    assert actual.second is not source.second


def test_uninitialized_frozen_slot_retains_getstate_error(backend: None) -> None:
    source = Frozen([1], [2])
    object.__delattr__(source, "first")
    with pytest.raises(AttributeError) as reference:
        copy.deepcopy(source)
    with pytest.raises(AttributeError) as actual:
        clone.deep(source)
    assert str(actual.value) == str(reference.value)


@pytest.mark.parametrize("record", [Row, Frozen])
def test_slot_backreferences_reuse_the_published_twin(
    backend: None, record: type[Row | Frozen]
) -> None:
    values: list[object] = []
    source = record(values, values)
    values.append(source)
    copied = clone.deep(source)
    assert isinstance(copied.first, list)
    assert copied.first is copied.second
    assert copied.first[0] is copied
    assert values == [source]


def test_slot_hooks_see_published_parent_before_fields_are_restored(
    backend: None,
) -> None:
    source: Row

    class Hook:
        def __deepcopy__(self, memo: dict[int, object]) -> object:
            parent = memo[id(source)]
            assert isinstance(parent, Row)
            assert not hasattr(parent, "first")
            return {"parent": parent}

    source = Row(Hook(), None)
    actual = clone.deep(source)
    assert isinstance(actual.first, dict)
    assert actual.first["parent"] is actual


def test_later_copy_hooks_take_precedence_over_warmed_slot_admission(
    backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Row([1], [2])
    clone.deep(source)
    called: list[dict[int, object]] = []
    sentinel = object()

    def overridden(_self: Row, memo: dict[int, object]) -> object:
        called.append(memo)
        return sentinel

    monkeypatch.setattr(Row, "__deepcopy__", overridden, raising=False)
    assert clone.deep(source) is sentinel
    assert len(called) == 1


def test_later_copyreg_takes_precedence_over_warmed_slot_admission(
    backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Row([1], [2])
    clone.deep(source)
    monkeypatch.setitem(copyreg.dispatch_table, Row, lambda _value: (list, ([9],)))
    actual: object = clone.deep(source)
    assert actual == [9]


def test_allocator_and_setstate_changes_keep_default_reconstruction_order(
    backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    @dataclasses.dataclass(slots=True)
    class Local:
        first: object
        second: object

    source = Local([1], [2])
    clone.deep(source)
    calls: list[str] = []

    def allocate(cls: type[Local]) -> Local:
        calls.append("new")
        return object.__new__(cls)

    def restore(self: Local, state: tuple[None, dict[str, object]]) -> None:
        calls.append("setstate")
        for name, value in state[1].items():
            object.__setattr__(self, name, value)

    monkeypatch.setattr(Local, "__new__", staticmethod(allocate))
    monkeypatch.setattr(Local, "__setstate__", restore, raising=False)
    expected = copy.deepcopy(source)
    expected_calls = list(calls)
    calls.clear()
    actual = clone.deep(source)
    assert actual == expected
    assert calls == expected_calls == ["new", "setstate"]


def test_custom_slot_reducers_remain_foreign(backend: None) -> None:
    class Foreign:
        def __deepcopy__(self, _memo: dict[int, object]) -> object:
            return ["foreign"]

    @dataclasses.dataclass(slots=True)
    class Custom:
        value: object

        @override
        def __reduce_ex__(
            self, _protocol: SupportsIndex
        ) -> tuple[type[Self], tuple[object]]:
            return type(self), (Foreign(),)

    source = Custom([1])
    assert clone.deep(source) == copy.deepcopy(source) == Custom(["foreign"])


@pytest.mark.parametrize("shape", ["iterator", "list_args"])
def test_admitted_slot_reducer_retains_iterable_reduction_and_factory_args(
    backend: None, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    @dataclasses.dataclass(slots=True)
    class Local:
        value: object

    class Reducer:
        def __get__(
            self, source: Local | None, owner: type[Local] | None = None
        ) -> object:
            # Ordinary class admission sees the default reducer; instance
            # lookup can still supply a different reconstruction recipe.
            if source is None:
                return object.__reduce_ex__

            def reduce(protocol: SupportsIndex) -> object:
                assert protocol == 4
                if shape == "iterator":
                    return iter((list, ([1],), None, None, None))
                return (list, [[1]], None, None, None)

            return reduce

    monkeypatch.setattr(Local, "__reduce_ex__", Reducer())
    source = Local(None)
    actual: object = clone.deep(source)
    expected: object = copy.deepcopy(source)
    assert actual == expected == [1]
    assert actual is not expected
