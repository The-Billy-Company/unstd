"""Persistent ``Map`` histories with every version retained against a dict snapshot.

Each derivation (set, delete, update, mutation context) starts from any retained
version, never only the newest. After every step all versions still equal the
immutable reference snapshot taken when they were made. Keys collide on purpose
so collision nodes are exercised, and faulting keys must leave every version and
an open mutation unchanged and usable.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)
import pytest

from tests.support.environment.backends import require_backend
from unstd.containers import Map


if TYPE_CHECKING:
    from collections.abc import Mapping

    from immutables import MapMutation


class Collider:
    """Equal by value, hashed into three buckets so the trie needs collision nodes."""

    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Collider) and self.value == other.value

    def __hash__(self) -> int:
        return self.value % 3

    def __repr__(self) -> str:
        return f"Collider({self.value})"


class RefusedError(Exception):
    """The one failure a faulting key raises, compared by identity."""


class Faulting(Collider):
    """Shares a bucket with real keys and refuses every equality test."""

    __slots__ = ("error",)

    def __init__(self, value: int, error: RefusedError) -> None:
        super().__init__(value)
        self.error = error

    def __eq__(self, other: object) -> bool:
        raise self.error

    __hash__ = Collider.__hash__


UNIVERSE = [Collider(value) for value in range(12)]
KEYS = st.sampled_from(UNIVERSE)
PAIRS = st.dictionaries(KEYS, st.integers(), max_size=8)
type Version = tuple[Map[Collider, int], Mapping[Collider, int]]


_ABSENT = object()


def _assert_mutation_holds(
    mutation: MapMutation[Collider, int], expected: Mapping[Collider, int]
) -> None:
    assert len(mutation) == len(expected)
    for key in UNIVERSE:
        assert mutation.get(key, _ABSENT) == expected.get(key, _ABSENT)


class MapHistory(RuleBasedStateMachine):
    versions = Bundle("versions")

    def __init__(self) -> None:
        super().__init__()
        self.retained: list[Version] = []

    def _keep(
        self, current: Map[Collider, int], expected: dict[Collider, int]
    ) -> Version:
        version = (current, MappingProxyType(dict(expected)))
        self.retained.append(version)
        return version

    @rule(target=versions, pairs=PAIRS)
    def build(self, pairs: dict[Collider, int]) -> Version:
        return self._keep(Map(pairs), pairs)

    @rule(target=versions, version=versions, key=KEYS, value=st.integers())
    def set(self, version: Version, key: Collider, value: int) -> Version:
        current, expected = version
        return self._keep(current.set(key, value), {**expected, key: value})

    @rule(target=versions, version=versions, key=KEYS)
    def delete(self, version: Version, key: Collider) -> Version:
        current, expected = version
        if key not in expected:
            with pytest.raises(KeyError):
                current.delete(key)
            return version
        return self._keep(
            current.delete(key), {k: v for k, v in expected.items() if k != key}
        )

    @rule(target=versions, version=versions, pairs=PAIRS)
    def update(self, version: Version, pairs: dict[Collider, int]) -> Version:
        current, expected = version
        return self._keep(current.update(pairs), {**expected, **pairs})

    @rule(
        target=versions,
        version=versions,
        edits=st.lists(st.tuples(KEYS, st.none() | st.integers()), max_size=8),
    )
    def mutate(
        self, version: Version, edits: list[tuple[Collider, int | None]]
    ) -> Version:
        current, expected = version
        draft = dict(expected)
        with current.mutate() as mutation:
            for key, value in edits:
                if value is None:
                    assert mutation.pop(key, None) == draft.pop(key, None)
                else:
                    mutation[key] = draft[key] = value
                _assert_mutation_holds(mutation, draft)
            derived = mutation.finish()
        with pytest.raises(ValueError, match="finished"):
            mutation.set(Collider(0), 0)
        return self._keep(derived, draft)

    @rule(version=versions, key=KEYS)
    def aborted_mutation(self, version: Version, key: Collider) -> None:
        current, _ = version
        error = RefusedError()
        with pytest.raises(RefusedError) as raised, current.mutate() as mutation:  # noqa: PT012 — exceptional mutation exit is under test.
            mutation[key] = -1
            raise error
        assert raised.value is error
        with pytest.raises(ValueError, match="finished"):
            mutation.set(key, 0)

    @rule(version=versions, value=st.integers(0, 11), extra=KEYS)
    def equality_fault(self, version: Version, value: int, extra: Collider) -> None:
        current, expected = version
        error = RefusedError()
        faulting = Faulting(value, error)
        if any(hash(key) == hash(faulting) for key in expected):
            with pytest.raises(RefusedError) as raised:
                current.set(faulting, 0)
            assert raised.value is error
            mutation = current.mutate()
            with pytest.raises(RefusedError) as raised:
                mutation.set(faulting, 0)
            assert raised.value is error
            _assert_mutation_holds(mutation, expected)
            mutation.set(extra, -7)
            assert dict(mutation.finish().items()) == {**expected, extra: -7}

    @rule(version=versions)
    def hash_fault(self, version: Version) -> None:
        current, _ = version
        with pytest.raises(TypeError):
            current.set([], 0)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            current.mutate().set({}, 0)  # type: ignore[arg-type]

    @invariant()
    def every_version_still_equals_its_snapshot(self) -> None:
        for current, expected in self.retained:
            assert len(current) == len(expected)
            assert dict(current.items()) == expected
            assert all(current[k] == v for k, v in expected.items())
            rebuilt = Map(dict(expected))
            assert current == rebuilt
            assert hash(current) == hash(rebuilt)
            assert Collider(99) not in current
            assert current.get(Collider(99), "absent") == "absent"


def test_retained_versions_stay_equal_to_their_snapshots() -> None:
    require_backend("immutables")
    run_state_machine_as_test(MapHistory)


def test_stored_values_are_shared_references_not_frozen() -> None:
    require_backend("immutables")
    payload: list[int] = []
    first = Map(k=payload)
    second = first.set("other", 1)
    payload.append(1)
    assert first["k"] is second["k"] is payload
    assert first["k"] == [1]
