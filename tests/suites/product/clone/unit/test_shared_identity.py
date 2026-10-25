"""Deep cloning preserves source aliases across structural and copy-hook boundaries."""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
from typing import Any

import pytest

from tests.support.environment.backends import require_backend
from unstd import clone
from unstd.clone import structural


@pytest.fixture(params=["python", "native"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "native":
        native = pytest.importorskip("unstd.clone._clone_native")
        monkeypatch.setattr(structural, "_native_clone", native.clone_checked)
    else:
        for name in list(vars(structural)):
            if name.startswith("_native_"):
                monkeypatch.setattr(structural, name, None)


@pytest.mark.parametrize("kind", [list, dict, tuple, set, frozenset, bytearray])
def test_repeated_nodes_are_copied_once_and_remain_independent(
    backend: None, kind: type
) -> None:
    shared = kind([1]) if kind is not dict else {"value": [1]}
    source = [shared, {"again": shared}, (shared,)]

    actual = clone.deep(source)

    assert actual == source
    assert actual[0] is actual[1]["again"] is actual[2][0]
    assert actual[0] is not shared
    if kind is list:
        actual[0].append(2)
        assert actual[1]["again"] == [1, 2]
        assert shared == [1]


def test_equal_but_distinct_nodes_stay_distinct_and_memos_are_per_call(
    backend: None,
) -> None:
    first = [1]
    second = [1]
    source = [first, second, first]

    actual = clone.deep(source)
    another = clone.deep(source)

    assert actual[0] is actual[2]
    assert actual[0] is not actual[1]
    assert actual[0] is not another[0]
    assert another[0] is another[2]
    assert actual[0] is not first
    assert actual[1] is not second


def test_dataclass_aliases_cross_record_state_and_outer_containers(
    backend: None,
) -> None:
    @dataclasses.dataclass
    class Record:
        first: list[int]
        second: list[int]

    shared = [1]
    record = Record(shared, shared)
    source = {"records": [record, record, Record(shared, shared)], "outside": shared}

    actual = clone.deep(source)
    first, repeated, other = actual["records"]

    assert first is repeated
    assert first is not record
    assert first is not other
    assert (
        first.first is first.second is other.first is other.second is actual["outside"]
    )
    assert actual["outside"] is not shared
    actual["outside"].append(2)
    assert shared == [1]


@pytest.mark.parametrize("foreign_first", [False, True])
def test_copy_hook_and_walked_siblings_share_one_memo(
    backend: None, foreign_first: bool
) -> None:
    calls: list[dict[int, Any]] = []

    class Foreign:
        def __init__(self, value: list[int]) -> None:
            self.value = value

        def __deepcopy__(self, memo: dict[int, Any]) -> Foreign:
            calls.append(memo)
            twin = object.__new__(type(self))
            memo[id(self)] = twin
            twin.value = copy.deepcopy(self.value, memo)
            return twin

    shared = [1]
    foreign = Foreign(shared)
    source = [foreign, shared, foreign] if foreign_first else [shared, foreign, foreign]

    actual = clone.deep(source)
    copied = actual[0] if foreign_first else actual[1]
    outside = actual[1] if foreign_first else actual[0]

    assert copied is actual[2]
    assert copied.value is outside
    assert outside is not shared
    assert copied is not foreign
    assert len(calls) == 1


@pytest.mark.parametrize("foreign_first", [False, True])
def test_default_foreign_copy_and_walked_siblings_share_one_memo(
    backend: None, foreign_first: bool
) -> None:
    class Foreign:
        def __init__(self, value: list[int]) -> None:
            self.value = value

    shared = [1]
    foreign = Foreign(shared)
    source = [foreign, shared] if foreign_first else [shared, foreign]

    actual = clone.deep(source)
    copied = actual[0] if foreign_first else actual[1]
    outside = actual[1] if foreign_first else actual[0]

    assert copied.value is outside
    assert outside is not shared
    assert copied is not foreign


def test_foreign_backreference_reuses_the_walked_root(backend: None) -> None:
    class Foreign:
        pass

    foreign = Foreign()
    source = [foreign]
    foreign.parent = source

    actual = clone.deep(source)

    assert actual is not source
    assert actual[0] is not foreign
    assert actual[0].parent is actual


def test_hashable_aliases_cross_dict_keys_sets_and_values(backend: None) -> None:
    class Key:
        def __init__(self) -> None:
            self.values = [1]

    key = Key()
    pair = (key,)
    source = {
        "value": key,
        key: pair,
        "set": {key},
        "frozen": frozenset({key}),
        pair: key,
    }

    actual = clone.deep(source)
    copied_key = actual["value"]
    copied_pair = actual[copied_key]

    assert copied_key is not key
    assert copied_key.values is not key.values
    assert copied_pair is not pair
    assert copied_pair[0] is copied_key
    assert actual[copied_pair] is copied_key
    assert next(iter(actual["set"])) is copied_key
    assert next(iter(actual["frozen"])) is copied_key


def test_copy_failure_does_not_reuse_partial_clones_on_the_next_call(
    backend: None,
) -> None:
    fail = True
    calls = 0

    class Foreign:
        def __deepcopy__(self, memo: dict[int, Any]) -> list[int]:
            nonlocal calls
            calls += 1
            if fail:
                msg = "copy failed"
                raise ValueError(msg)
            return [calls]

    foreign = Foreign()
    source = [foreign, foreign]
    with pytest.raises(ValueError, match="copy failed"):
        clone.deep(source)
    fail = False

    actual = clone.deep(source)

    assert actual[0] is actual[1]
    assert actual[0] == [2]
    assert calls == 2


def test_asdict_keeps_dataclasses_asdict_alias_expansion(backend: None) -> None:
    @dataclasses.dataclass
    class Record:
        first: list[int]
        second: list[int]

    shared = [1]
    source = Record(shared, shared)

    actual = clone.asdict(source)

    assert actual == dataclasses.asdict(source)
    assert actual["first"] is not actual["second"]
    assert actual["first"] is not shared


def test_pydantic_aliases_cross_fields_extras_private_state_and_siblings(
    backend: None,
) -> None:
    pydantic = require_backend("pydantic")

    class Record(pydantic.BaseModel, extra="allow"):
        first: Any
        second: Any
        _cache: Any = pydantic.PrivateAttr()

    shared = [1]
    record = Record(first=shared, second=shared, extra_value=shared)
    record._cache = shared

    actual = clone.deep([record, shared, record])
    copied = actual[0]

    assert copied is actual[2]
    assert copied is not record
    assert (
        copied.first
        is copied.second
        is copied.extra_value
        is copied._cache
        is actual[1]
    )
    assert actual[1] is not shared


def test_msgspec_aliases_cross_reducer_arguments_and_siblings(backend: None) -> None:
    msgspec = require_backend("msgspec")

    class Record(msgspec.Struct):
        first: Any
        second: Any

    shared = [1]
    record = Record(shared, shared)

    actual = clone.deep([record, shared, record])
    copied = actual[0]

    assert copied is actual[2]
    assert copied is not record
    assert copied.first is copied.second is actual[1]
    assert actual[1] is not shared


@pytest.mark.parametrize("foreign_first", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        dt.date(2026, 10, 25),
        dt.timedelta(days=2, seconds=3),
        dt.timezone(dt.timedelta(hours=3)),
        dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
        dt.time(12, 30, tzinfo=dt.UTC),
    ],
)
def test_immutable_temporal_aliases_cross_foreign_copy_boundaries(
    backend: None, foreign_first: bool, value: object
) -> None:
    class Foreign:
        def __init__(self, value: object) -> None:
            self.value = value

    foreign = Foreign(value)
    source = [foreign, value] if foreign_first else [value, foreign]

    actual = clone.deep(source)
    copied = actual[0] if foreign_first else actual[1]
    outside = actual[1] if foreign_first else actual[0]

    assert copied is not foreign
    assert copied.value is outside
    assert outside == value


@pytest.mark.parametrize("private_first", [False, True])
@pytest.mark.parametrize("undefined", [False, True])
@pytest.mark.parametrize("subclassed", [False, True])
def test_pydantic_private_views_preserve_outer_dictionary_values(
    backend: None, private_first: bool, undefined: bool, subclassed: bool
) -> None:
    pydantic = require_backend("pydantic")
    pydantic_core = require_backend("pydantic_core")

    class Record(pydantic.BaseModel):
        _cache: Any = pydantic.PrivateAttr()

    shared = [1]
    record = Record()
    record._cache = shared
    private = record.__pydantic_private__
    if subclassed:

        class PrivateMap(dict):
            pass

        private = PrivateMap(private)
        object.__setattr__(record, "__pydantic_private__", private)
    if undefined:
        private["undefined"] = pydantic_core.PydanticUndefined
    source = [private, record, shared] if private_first else [record, private, shared]

    actual = clone.deep(source)
    copied = actual[1] if private_first else actual[0]
    outside = actual[0] if private_first else actual[1]

    assert copied is not record
    assert type(outside) is type(private)
    assert type(copied.__pydantic_private__) is dict
    assert "undefined" not in copied.__pydantic_private__
    if undefined or subclassed:
        assert copied.__pydantic_private__ is not outside
    else:
        assert copied.__pydantic_private__ is outside
    if undefined:
        assert outside["undefined"] is pydantic_core.PydanticUndefined
    else:
        assert "undefined" not in outside
    assert outside["_cache"] is copied._cache is actual[2]
    assert actual[2] is not shared
    if undefined:
        assert private["undefined"] is pydantic_core.PydanticUndefined


@pytest.mark.parametrize("foreign_first", [False, True])
@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
def test_immutable_temporal_timezone_aliases_cross_foreign_copy_boundaries(
    backend: None, foreign_first: bool, kind: type
) -> None:
    class Foreign:
        def __init__(self, zone: dt.timezone) -> None:
            self.zone = zone

    zone = dt.timezone(dt.timedelta(hours=3))
    value = (
        dt.datetime(2026, 10, 25, 12, tzinfo=zone)
        if kind is dt.datetime
        else dt.time(12, 30, tzinfo=zone)
    )
    foreign = Foreign(zone)
    source = [foreign, value] if foreign_first else [value, foreign]

    actual = clone.deep(source)
    copied = actual[0] if foreign_first else actual[1]
    temporal = actual[1] if foreign_first else actual[0]

    assert copied is not foreign
    assert temporal.tzinfo is copied.zone
    assert temporal == value


@pytest.mark.parametrize("private_first", [False, True])
def test_pydantic_private_parent_memo_is_restored_after_a_descendant_override(
    backend: None, private_first: bool
) -> None:
    pydantic = require_backend("pydantic")
    calls = 0
    private: Any = None

    class Hook:
        def __deepcopy__(self, memo: dict[int, Any]) -> list[int]:
            nonlocal calls
            calls += 1
            memo[id(private)] = ["override"]
            return [7]

    class Record(pydantic.BaseModel):
        _cache: Any = pydantic.PrivateAttr()

    record = Record()
    record._cache = Hook()
    private = record.__pydantic_private__
    source = [private, record] if private_first else [record, private]

    actual = clone.deep(source)
    copied = actual[1] if private_first else actual[0]
    outside = actual[0] if private_first else actual[1]

    assert type(outside) is dict
    assert copied.__pydantic_private__ is outside
    assert outside == {"_cache": [7]}
    assert calls == 1
    assert type(private["_cache"]) is Hook
