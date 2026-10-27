"""Native temporal eligibility preserves Python's actual membership callbacks."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal, override
import weakref

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from unstd import clone
from unstd.clone import structural


native = pytest.importorskip("unstd.clone._clone_native")


type _Shape = Literal["list", "tuple", "dict", "set"]
type _Container = list[object] | tuple[object, ...] | dict[str, object] | set[object]
type _Temporal = dt.datetime | dt.time


def _container_source(
    shape: _Shape, values: tuple[object, object], names: tuple[str, str]
) -> _Container:
    if shape == "dict":
        return dict(zip(names, values, strict=True))
    if shape == "list":
        return list(values)
    if shape == "tuple":
        return values
    return set(values)


@settings(max_examples=120, deadline=None)
@given(...)
def test_native_temporal_membership_keeps_the_python_probe_trace(
    policy: Annotated[
        Literal["_ATOMS", "_GRAPH_ATOMS", "_TIMEZONES"],
        st.sampled_from(["_ATOMS", "_GRAPH_ATOMS", "_TIMEZONES"]),
    ],
    kind: Annotated[type[_Temporal], st.sampled_from([dt.datetime, dt.time])],
    utc: bool,
    foreign_count: Annotated[int, st.integers(1, 6)],
    included: bool,
    first: bool,
    outcome: Annotated[
        Literal["false", "true", "error"], st.sampled_from(["false", "true", "error"])
    ],
) -> None:
    zone = dt.UTC if utc else None
    value = (
        dt.datetime(2026, 1, 1, tzinfo=zone)
        if kind is dt.datetime
        else dt.time(1, 2, 3, tzinfo=zone)
    )
    selected = type(zone) if policy == "_TIMEZONES" else kind
    armed = False
    calls: list[object] = []
    failure = LookupError("foreign temporal policy comparison")

    class Meta(type):
        @override
        def __hash__(cls) -> int:
            return hash(selected)

        @override
        def __eq__(cls, other: object) -> bool:
            if armed:
                calls.append(other)
                if outcome == "error":
                    raise failure
                return outcome == "true"
            return False

    keys: list[type[object]] = [
        Meta(f"Foreign{index}", (), {}) for index in range(foreign_count)
    ]
    if included:
        keys.insert(0 if first else len(keys), selected)
    replacement = frozenset(keys)
    armed = True
    source = [value, value]

    def observed(
        use_native: bool,
    ) -> tuple[list[_Temporal] | None, list[object], LookupError | None]:
        calls.clear()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(structural, policy, replacement)
            if not use_native:
                patch.setattr(structural, "_native_clone", None)
            try:
                result = clone.deep(source)
            except LookupError as error:
                return None, calls.copy(), error
            return result, calls.copy(), None

    expected, expected_calls, expected_error = observed(False)
    actual, actual_calls, actual_error = observed(True)
    assert actual_calls == expected_calls
    assert actual_error is expected_error
    if expected_error is not None:
        assert expected_error is failure
    else:
        assert actual is not None
        assert expected is not None
        assert type(actual) is type(expected)
        assert actual == expected
        assert actual[0] is actual[1]
        assert (actual[0] is value) == (expected[0] is value)


def test_temporal_namespace_str_subclasses_keep_the_safe_fallback() -> None:
    comparisons: list[object] = []
    armed = False

    class ForeignName(str):
        @override
        def __hash__(self) -> int:
            return hash("_TEMPORAL_WALK")

        @override
        def __eq__(self, other: object) -> bool:
            if armed:
                comparisons.append(other)
                raise AssertionError
            return False

    key = ForeignName("foreign namespace entry")
    value = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    # The Python branch never reads the private certificate name. A foreign
    # string key sharing its hash must not make native eligibility compare it.
    namespace = vars(structural)
    certificate = namespace.pop("_TEMPORAL_WALK")
    namespace[key] = object()
    armed = True
    try:
        actual = clone.deep([value, value])
        assert actual[0] is actual[1] is value
        assert comparisons == []
    finally:
        armed = False
        del namespace[key]
        namespace["_TEMPORAL_WALK"] = certificate


@pytest.mark.parametrize("shape", ["list", "tuple", "dict", "set"])
@pytest.mark.parametrize("foreign_namespace", [False, True])
def test_temporal_parent_membership_uses_live_walker_and_policy_lifetime(
    shape: _Shape, foreign_namespace: bool
) -> None:
    value = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    source = _container_source(shape, (value, value), ("first", "again"))
    original_policy = structural._GRAPH_ATOMS
    original_walk = structural._walk
    events: list[str] = []
    namespace = vars(structural)
    foreign_name = object()

    def changed_walk(o: object, memo: dict[int, Any] | None = None) -> str:
        events.append("walk")
        return "changed"

    class Meta(type):
        @override
        def __hash__(cls) -> int:
            return hash(dt.datetime)

        @override
        def __eq__(cls, other: object) -> bool:
            events.append("contains")
            # Direct assignment leaves no fixture-owned reference to the old
            # policy; its weakref callback must run before the new walker.
            structural._GRAPH_ATOMS = original_policy
            structural._walk = changed_walk
            if foreign_namespace:
                # Deliberately violate the string-key namespace boundary;
                # native eligibility must preserve its ordinary lookups.
                namespace[foreign_name] = "foreign"  # type: ignore[index]
            return False

    class Foreign(metaclass=Meta):
        pass

    observations: list[tuple[_Container, list[str]]] = []
    for enabled in (False, True):
        with pytest.MonkeyPatch.context() as active_patch:
            active_patch.setattr(structural, "_walk", original_walk)
            replacement = frozenset({Foreign})
            released = weakref.ref(replacement, lambda _ref: events.append("released"))
            active_patch.setattr(structural, "_GRAPH_ATOMS", replacement)
            del replacement
            if not enabled:
                active_patch.setattr(structural, "_native_clone", None)
            events.clear()
            try:
                actual = clone.deep(source)
                assert released() is None
                observations.append((actual, events.copy()))
            finally:
                if foreign_name in namespace:
                    # Reap the intentionally foreign key on every exit.
                    del namespace[foreign_name]
    assert observations[0] == observations[1]
    expected = ["contains", "released", "walk"]
    if shape != "set":
        expected.append("walk")
    assert observations[0][1] == expected


@pytest.mark.parametrize("shape", ["list", "tuple", "dict"])
@pytest.mark.parametrize("changed", ["pointer", "code"])
@pytest.mark.parametrize("graph_atom", [False, True])
def test_prior_hook_changes_the_active_parent_temporal_dispatch(
    shape: Literal["list", "tuple", "dict"], changed: str, graph_atom: bool
) -> None:
    value = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    original_walk = structural._walk
    original_code = original_walk.__code__
    original_policy = structural._GRAPH_ATOMS

    def changed_walk(o: object, memo: dict[int, Any] | None = None) -> str:
        return "changed"

    class Hook:
        def __deepcopy__(self, _memo: dict[int, Any]) -> str:
            if changed == "pointer":
                structural._walk = changed_walk
            else:
                structural._walk.__code__ = changed_walk.__code__
            if graph_atom:
                structural._GRAPH_ATOMS = original_policy | {dt.datetime}
            return "copied hook"

    hook = Hook()
    source = _container_source(shape, (hook, value), ("hook", "stamp"))
    results: list[_Container] = []
    for enabled in (False, True):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(structural, "_walk", original_walk)
            patch.setattr(original_walk, "__code__", original_code)
            patch.setattr(structural, "_GRAPH_ATOMS", original_policy)
            if not enabled:
                patch.setattr(structural, "_native_clone", None)
            results.append(clone.deep(source))
    assert type(results[0]) is type(results[1]) is type(source)
    assert results[0] == results[1]
    copied = results[1]
    if shape == "dict":
        assert isinstance(copied, dict)
        stamp = copied["stamp"]
    else:
        assert isinstance(copied, (list, tuple))
        stamp = copied[1]
    if graph_atom:
        assert stamp is value
    else:
        assert stamp == "changed"


def test_dynamic_temporal_parent_preserves_namespace_equality_exception() -> None:
    value = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    source = [value, value]
    original_walk = structural._walk
    original_policy = structural._GRAPH_ATOMS
    namespace = vars(structural)
    failure = LookupError("dynamic graph-atom namespace comparison")
    armed = False
    events: list[str] = []

    def changed_walk(o: object, memo: dict[int, Any] | None = None) -> str:
        events.append("walk")
        return "changed"

    class ForeignName(str):
        @override
        def __hash__(self) -> int:
            return hash("_GRAPH_ATOMS")

        @override
        def __eq__(self, other: object) -> bool:
            if armed:
                events.append("namespace")
                raise failure
            return False

    name = ForeignName("foreign graph-atom name")

    class Meta(type):
        @override
        def __hash__(cls) -> int:
            return hash(dt.datetime)

        @override
        def __eq__(cls, other: object) -> bool:
            events.append("contains")
            del namespace["_GRAPH_ATOMS"]
            namespace[name] = object()
            structural._walk = changed_walk
            return False

    class Foreign(metaclass=Meta):
        pass

    observations: list[list[str]] = []
    for enabled in (False, True):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(structural, "_walk", original_walk)
            patch.setattr(structural, "_GRAPH_ATOMS", frozenset({Foreign}))
            if not enabled:
                patch.setattr(structural, "_native_clone", None)
            events.clear()
            armed = True
            try:
                with pytest.raises(LookupError) as observed:
                    clone.deep(source)
                assert observed.value is failure
                observations.append(events.copy())
            finally:
                armed = False
                del namespace[name]
                namespace["_GRAPH_ATOMS"] = original_policy
    assert observations[0] == observations[1] == ["contains", "walk", "namespace"]
