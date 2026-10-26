"""The canonical native temporal walk preserves Python memo and hook policy."""

from __future__ import annotations

import copy
import datetime as dt
import sys
from typing import Any

import pytest

from unstd.clone import structural


native = pytest.importorskip("unstd.clone._clone_native")


def _value(kind: type[dt.datetime | dt.time], zone: dt.tzinfo | None) -> Any:
    if kind is dt.datetime:
        return dt.datetime(2026, 1, 1, 2, 3, 4, 56789, tzinfo=zone, fold=1)
    return dt.time(2, 3, 4, 56789, tzinfo=zone, fold=1)


def _native(source: Any, memo: dict[int, Any]) -> Any:
    return native.clone_walk_canonical_graph(
        source, structural._walk, structural._record_admit, memo
    )


def _fallback_calls(value: Any) -> list[Any]:
    calls: list[Any] = []
    previous = sys.getprofile()

    def profile(frame: Any, event: str, argument: Any) -> None:
        if (
            event == "call"
            and frame.f_code is structural._walk.__code__
            and frame.f_locals.get("o") is value
        ):
            calls.append(value)

    sys.setprofile(profile)
    try:
        assert _native([value], {})[0] is value
    finally:
        sys.setprofile(previous)
    return calls


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("zone", [None, dt.UTC])
def test_canonical_temporal_memo(kind: Any, zone: Any) -> None:
    value = _value(kind, zone)
    result = _native([value, value], {})
    assert result[0] is result[1] is value
    explicit = object()
    assert _native([value, value], {id(value): explicit}) == [explicit, explicit]
    assert _fallback_calls(value) == []


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("override", ["zone", "offset"])
def test_canonical_temporal_zone_memo(kind: Any, override: str) -> None:
    value = _value(kind, dt.UTC)
    replacement = dt.timezone(dt.timedelta(hours=2))
    offset = dt.UTC.utcoffset(None)
    memo = (
        {id(dt.UTC): replacement}
        if override == "zone"
        else {id(offset): dt.timedelta(hours=2)}
    )
    source = [value, value, dt.UTC, offset]
    reference = structural._walk(source, dict(memo))
    result = _native(source, dict(memo))
    assert result == reference
    assert result[0] is result[1]
    assert result[0].tzinfo is result[2]
    assert result[0].utcoffset() == dt.timedelta(hours=2)
    assert result[0].fold == value.fold
    assert result[0].microsecond == value.microsecond


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("table", ["dispatch_table", "_deepcopy_dispatch"])
def test_temporal_registry_override_keeps_python_policy(
    monkeypatch: pytest.MonkeyPatch, kind: Any, table: str
) -> None:
    value = _value(kind, dt.UTC)
    sentinel = object()
    override: Any = (
        (lambda obj: (lambda: sentinel, ()))
        if table == "dispatch_table"
        else (lambda obj, memo: sentinel)
    )
    monkeypatch.setitem(getattr(copy, table), kind, override)
    reference = structural._walk([value, value], {})
    result = _native([value, value], {})
    assert result == reference
    assert result[0] is result[1] is reference[0] is value
    assert _fallback_calls(value) == []


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("zone", [None, dt.UTC])
@pytest.mark.parametrize("table", ["dispatch_table", "_deepcopy_dispatch"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyError])
def test_irrelevant_temporal_registry_keys_are_not_compared(
    monkeypatch: pytest.MonkeyPatch,
    kind: Any,
    zone: Any,
    table: str,
    error_type: type[Exception],
) -> None:
    """The immutable structural branch never probes copy's foreign keys."""
    value = _value(kind, zone)
    comparisons: list[object] = []
    sentinel = error_type("unrelated copy registry key compared")

    class Meta(type):
        def __hash__(cls):
            return hash(kind)

        def __eq__(cls, other):
            comparisons.append(other)
            raise sentinel

    class Foreign(metaclass=Meta):
        pass

    monkeypatch.setitem(getattr(copy, table), Foreign, object())
    source = [value, value]
    reference = structural._walk(source, {})
    assert comparisons == []
    actual = _native(source, {})
    assert actual == reference
    assert actual[0] is actual[1] is value
    assert comparisons == []


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("zone", [None, dt.UTC])
def test_rebound_timezone_policy_can_select_copy_registry(
    monkeypatch: pytest.MonkeyPatch, kind: Any, zone: Any
) -> None:
    value = _value(kind, zone)
    sentinel = object()
    monkeypatch.setattr(structural, "_TIMEZONES", frozenset())
    monkeypatch.setitem(copy._deepcopy_dispatch, kind, lambda obj, memo: sentinel)
    source = [value, value]
    reference = structural._walk(source, {})
    actual = _native(source, {})
    assert actual == reference == [sentinel, sentinel]


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
@pytest.mark.parametrize("zone", [None, dt.UTC])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyError])
def test_timezone_policy_foreign_keys_keep_python_exception(
    monkeypatch: pytest.MonkeyPatch,
    kind: Any,
    zone: Any,
    error_type: type[Exception],
) -> None:
    value = _value(kind, zone)
    comparisons: list[object] = []
    sentinel = error_type("timezone policy key compared")

    class Meta(type):
        def __hash__(cls):
            return hash(type(zone))

        def __eq__(cls, other):
            comparisons.append(other)
            raise sentinel

    class Foreign(metaclass=Meta):
        pass

    monkeypatch.setattr(structural, "_TIMEZONES", frozenset({Foreign}))
    with pytest.raises(error_type) as reference:
        structural._walk([value], {})
    expected_comparisons = comparisons.copy()
    comparisons.clear()
    with pytest.raises(error_type) as actual:
        _native([value], {})
    assert actual.value is reference.value is sentinel
    assert comparisons == expected_comparisons == [type(zone)]


@pytest.mark.parametrize("canonical", [False, True])
def test_custom_temporal_walker_is_called(canonical: bool) -> None:
    value = _value(dt.datetime, dt.UTC)
    sentinel = object()
    seen: list[Any] = []

    def walk(obj: Any, memo: dict[int, Any]) -> Any:
        seen.append(obj)
        return sentinel

    method = native.clone_walk_canonical_graph if canonical else native.clone_walk_graph
    assert method([value], walk, structural._record_admit, {}) == [sentinel]
    assert seen == [value]


def test_custom_timezone_and_datetime_subclass_keep_copy_protocols() -> None:
    calls: list[str] = []

    class Zone(dt.tzinfo):
        def utcoffset(self, value: Any) -> dt.timedelta:
            return dt.timedelta(0)

        def dst(self, value: Any) -> dt.timedelta:
            return dt.timedelta(0)

        def __deepcopy__(self, memo: dict[int, Any]) -> dt.tzinfo:
            calls.append("zone")
            return dt.timezone(dt.timedelta(hours=2))

    class Stamp(dt.datetime):
        def __deepcopy__(self, memo: dict[int, Any]) -> str:
            calls.append("stamp")
            return "stamp copied"

    source = [dt.datetime(2026, 1, 1, tzinfo=Zone()), Stamp(2026, 1, 1)]
    assert _native(source, {}) == structural._walk(source, {})
    assert calls == ["zone", "stamp"] * 2


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
def test_naive_temporal_ignores_none_memo(kind: Any) -> None:
    value = _value(kind, None)
    assert _native([value], {id(None): object()})[0] is value


@pytest.mark.parametrize("name", ["datetime", "time"])
def test_changed_temporal_binding_uses_python_policy(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    kind = getattr(dt, name)
    value = _value(kind, dt.UTC)

    class Other:
        pass

    monkeypatch.setattr(dt, name, Other)
    reference = structural._walk([value], {})
    result = _native([value], {})
    assert result == reference
    assert result[0] is not value
    assert reference[0] is not value


def test_replaced_deepcopy_keeps_python_temporal_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _value(dt.datetime, dt.UTC)

    def refuse(*args: Any) -> Any:
        pytest.fail("Python's immutable temporal policy must remain authoritative")

    monkeypatch.setattr(copy, "deepcopy", refuse)
    assert structural._walk([value], {})[0] is value
    assert _fallback_calls(value) == []


def test_canonical_temporal_keepalive_order_matches_python() -> None:
    value = _value(dt.datetime, dt.UTC)
    source = [value, value, dt.UTC, dt.UTC.utcoffset(None)]
    expected_memo: dict[int, Any] = {}
    actual_memo: dict[int, Any] = {}
    expected = structural._walk(source, expected_memo)
    actual = _native(source, actual_memo)
    expected_pins = expected_memo.pop(id(expected_memo))
    actual_pins = actual_memo.pop(id(actual_memo))
    assert [id(node) for node in actual_pins] == [id(node) for node in expected_pins]
    assert actual_memo.keys() == expected_memo.keys()
    assert actual == expected
    assert actual[0] is actual[1]


def test_changed_timezone_binding_and_offset_memo_keep_python_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _value(dt.datetime, dt.UTC)
    offset = dt.UTC.utcoffset(None)
    memo = {id(offset): dt.timedelta(hours=2)}

    class OtherTimezone:
        pass

    monkeypatch.setattr(dt, "timezone", OtherTimezone)
    expected = structural._walk([value], dict(memo))
    actual = _native([value], dict(memo))
    assert actual == expected
    assert actual[0] is expected[0] is value
    assert _fallback_calls(value) == [value]


def test_stale_native_extension_keeps_shared_memo_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    from pathlib import Path
    import types

    legacy = types.ModuleType("unstd.clone._clone_native")
    for name in dir(native):
        if not name.startswith("__") and name != "clone_walk_canonical_graph":
            setattr(legacy, name, getattr(native, name))
    monkeypatch.setitem(sys.modules, "unstd.clone._clone_native", legacy)
    spec = importlib.util.spec_from_file_location(
        "_clone_legacy_extension_control", Path(structural.__file__)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._native_walk_graph is native.clone_walk_graph
    assert module._NATIVE_SELECTED_MEMO is True
    shared = [1, 2]
    copied = module.deep([shared, shared])
    assert copied[0] is copied[1]
    assert copied[0] is not shared


def test_foreign_memo_equality_mutation_keeps_original_lookup_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _value(dt.datetime, dt.UTC)
    offset = dt.UTC.utcoffset(None)
    original_timezone = dt.timezone

    class OtherTimezone:
        pass

    calls = 0

    class ForeignKey:
        def __hash__(self) -> int:
            return id(value)

        def __eq__(self, other: object) -> bool:
            nonlocal calls
            if other == id(value):
                calls += 1
                monkeypatch.setattr(dt, "timezone", OtherTimezone)
            return False

    # Reuse every identity key: dictionary probe paths can revisit a colliding
    # foreign key, so different source/memo addresses change callback counts.
    memo: dict[Any, Any] = {}
    source = [value]
    foreign_key = ForeignKey()

    def run(use_native: bool) -> tuple[Any, int]:
        nonlocal calls
        monkeypatch.setattr(dt, "timezone", original_timezone)
        memo.clear()
        calls = 0
        memo[foreign_key] = object()
        memo[id(offset)] = dt.timedelta(hours=2)
        result = _native(source, memo) if use_native else structural._walk(source, memo)
        return result, calls

    expected, expected_calls = run(False)
    actual, actual_calls = run(True)
    assert actual == expected
    assert actual[0] is expected[0] is value
    assert actual_calls == expected_calls
    assert actual_calls > 0


@pytest.mark.parametrize("kind", [dt.datetime, dt.time])
def test_record_state_key_equality_cannot_reenable_temporal_shortcut(
    monkeypatch: pytest.MonkeyPatch, kind: Any
) -> None:
    import dataclasses

    value = _value(kind, dt.UTC)
    offset = dt.UTC.utcoffset(None)
    original_timezone = dt.timezone

    @dataclasses.dataclass
    class Record:
        stamp: Any

    class OtherTimezone:
        pass

    memo: dict[Any, Any] = {}
    active = False
    key_calls = 0
    memo_calls = 0

    class ForeignMemoKey:
        def __hash__(self) -> int:
            return id(value)

        def __eq__(self, other: object) -> bool:
            nonlocal memo_calls
            if other == id(value):
                memo_calls += 1
                monkeypatch.setattr(dt, "timezone", OtherTimezone)
            return False

    memo_key = ForeignMemoKey()

    class StateKey:
        def __hash__(self) -> int:
            return hash("first")

        def __eq__(self, other: object) -> bool:
            nonlocal key_calls
            if active and other == "first":
                key_calls += 1
                memo[memo_key] = object()
                memo[id(offset)] = dt.timedelta(hours=2)
            return False

        def __deepcopy__(self, graph_memo: dict[int, Any]) -> Any:
            return self

    # Keep the source, state keys and memo identical across both executions so
    # this compares callback order rather than allocator-dependent probe paths.
    source = Record(value)
    state = source.__dict__
    state.clear()
    state["first"] = 1
    state[StateKey()] = 2
    state["stamp"] = value
    active = True

    def run(use_native: bool) -> tuple[Any, int, int]:
        nonlocal key_calls, memo_calls
        monkeypatch.setattr(dt, "timezone", original_timezone)
        memo.clear()
        key_calls = 0
        memo_calls = 0
        if use_native:
            copied = _native(source, memo)
        else:
            with monkeypatch.context() as patch:
                patch.setattr(structural, "_native_clone", None)
                copied = structural._walk(source, memo)
        return copied.stamp, key_calls, memo_calls

    expected, expected_key_calls, expected_memo_calls = run(False)
    actual, actual_key_calls, actual_memo_calls = run(True)
    assert actual is expected is value
    assert actual_key_calls == expected_key_calls
    assert actual_memo_calls == expected_memo_calls
    assert actual_key_calls > 0
    assert actual_memo_calls > 0
