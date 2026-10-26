"""Sorted collections against list/bisect, dict and set reference models.

Each machine replays generated insert/delete/update/range/index/bisect histories
on the re-exported class and on a plain reference, then checks the whole
container after every step. Comparison, key and hash faults must propagate with
identity and leave both the container and its later usability intact.
"""

from __future__ import annotations

import bisect
from collections import Counter
from functools import total_ordering

from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
import pytest

from tests.support.environment.backends import require_backend
from unstd.containers import SortedDict, SortedList, SortedSet


VALUES = st.integers(-12, 12)
BOUNDS = st.integers(-14, 14)


class RefusedError(Exception):
    """The one failure a fault double raises, compared by identity."""


@total_ordering
class Poison:
    """Orders as its integer but raises at one scheduled comparison, then never again."""

    def __init__(self, value: int, fail_at: int, error: RefusedError) -> None:
        self.value, self.fail_at, self.error = value, fail_at, error
        self.comparisons = 0

    def _other(self, other: object) -> int:
        self.comparisons += 1
        if self.comparisons == self.fail_at:
            raise self.error
        return other.value if isinstance(other, Poison) else other  # type: ignore[return-value]

    def __eq__(self, other: object) -> bool:
        return self.value == (other.value if isinstance(other, Poison) else other)

    def __lt__(self, other: object) -> bool:
        return self.value < self._other(other)

    def __hash__(self) -> int:
        return hash(self.value)


def _ranged(
    values: list[int], lo: int, hi: int, inclusive: tuple[bool, bool]
) -> list[int]:
    low, high = inclusive
    return [
        v
        for v in values
        if (lo <= v if low else lo < v) and (v <= hi if high else v < hi)
    ]


class SortedListMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.sl = SortedList()
        self.model: list[int] = []

    @rule(value=VALUES)
    def add(self, value: int) -> None:
        self.sl.add(value)
        bisect.insort(self.model, value)

    @rule(values=st.lists(VALUES, max_size=12))
    def update(self, values: list[int]) -> None:
        self.sl.update(values)
        self.model = sorted(self.model + values)

    @rule(value=VALUES)
    def remove(self, value: int) -> None:
        if value in self.model:
            self.sl.remove(value)
            self.model.remove(value)
        else:
            with pytest.raises(ValueError, match=r"(?:is )?not in list"):
                self.sl.remove(value)

    @rule(value=VALUES)
    def discard(self, value: int) -> None:
        self.sl.discard(value)
        if value in self.model:
            self.model.remove(value)

    @precondition(lambda self: self.model)
    @rule(data=st.data())
    def pop(self, data: st.DataObject) -> None:
        index = data.draw(st.integers(-len(self.model), len(self.model) - 1))
        assert self.sl.pop(index) == self.model.pop(index)

    @rule(start=st.integers(-15, 15), stop=st.integers(-15, 15))
    def delete_slice(self, start: int, stop: int) -> None:
        del self.sl[start:stop]
        del self.model[start:stop]

    @rule(value=VALUES)
    def bisect_and_rank(self, value: int) -> None:
        assert self.sl.bisect_left(value) == bisect.bisect_left(self.model, value)
        assert self.sl.bisect_right(value) == bisect.bisect_right(self.model, value)
        assert self.sl.count(value) == self.model.count(value)
        assert (value in self.sl) is (value in self.model)
        if value in self.model:
            assert self.sl.index(value) == self.model.index(value)
        else:
            with pytest.raises(ValueError, match=r"(?:is )?not in list"):
                self.sl.index(value)

    @rule(lo=BOUNDS, hi=BOUNDS, inclusive=st.tuples(st.booleans(), st.booleans()))
    def irange(self, lo: int, hi: int, inclusive: tuple[bool, bool]) -> None:
        expected = _ranged(self.model, lo, hi, inclusive)
        assert list(self.sl.irange(lo, hi, inclusive)) == expected
        assert list(self.sl.irange(lo, hi, inclusive, reverse=True)) == expected[::-1]

    @rule(start=st.integers(-15, 15), stop=st.integers(-15, 15))
    def index_slices(self, start: int, stop: int) -> None:
        assert self.sl[start:stop] == self.model[start:stop]
        if -len(self.model) <= start < len(self.model):
            assert self.sl[start] == self.model[start]
        else:
            with pytest.raises(IndexError):
                self.sl[start]

    @precondition(lambda self: self.model)
    @rule(value=VALUES, fail_at=st.integers(1, 8))
    def comparison_fault(self, value: int, fail_at: int) -> None:
        error = RefusedError()
        poison = Poison(value, fail_at, error)
        try:
            self.sl.add(poison)
        except RefusedError as raised:
            assert raised is error  # noqa: PT017 — scheduled faults may succeed or raise.
            assert poison.comparisons == fail_at
        else:
            assert poison.comparisons < fail_at
            bisect.insort(self.model, value)
        poison.fail_at = 0

    @rule(values=st.lists(VALUES, min_size=1, max_size=6))
    def update_fault(self, values: list[int]) -> None:
        # Upstream merges a large batch into its lists before sorting, so only a
        # fault while sorting the batch itself is rejected before mutation.
        error = RefusedError()
        with pytest.raises(RefusedError) as raised:
            self.sl.update([*values, Poison(0, 1, error)])
        assert raised.value is error

    @invariant()
    def matches_model(self) -> None:
        assert list(self.sl) == self.model
        assert len(self.sl) == len(self.model)
        assert list(reversed(self.sl)) == self.model[::-1]


def _rank(value: int) -> int:
    if value == 99:
        raise KEY_FAULT
    return -(value // 3)


KEY_FAULT = RefusedError()


class SortedKeyListMachine(RuleBasedStateMachine):
    """Order is by key only: values sharing a key may appear in any relative order."""

    def __init__(self) -> None:
        super().__init__()
        self.sl = SortedList(key=_rank)
        self.model: list[int] = []

    @rule(value=VALUES)
    def add(self, value: int) -> None:
        self.sl.add(value)
        self.model.append(value)

    @rule(values=st.lists(VALUES, max_size=12))
    def update(self, values: list[int]) -> None:
        self.sl.update(values)
        self.model += values

    @rule(value=VALUES)
    def remove(self, value: int) -> None:
        if value in self.model:
            self.sl.remove(value)
            self.model.remove(value)
        else:
            with pytest.raises(ValueError, match=r"(?:is )?not in list"):
                self.sl.remove(value)

    @precondition(lambda self: self.model)
    @rule(data=st.data())
    def pop(self, data: st.DataObject) -> None:
        keys = sorted(map(_rank, self.model))
        index = data.draw(st.integers(-len(keys), len(keys) - 1))
        popped = self.sl.pop(index)
        assert _rank(popped) == keys[index]
        self.model.remove(popped)

    @rule(value=VALUES)
    def bisect_and_rank(self, value: int) -> None:
        keys = sorted(map(_rank, self.model))
        assert self.sl.bisect_key_left(_rank(value)) == bisect.bisect_left(
            keys, _rank(value)
        )
        assert self.sl.bisect_key_right(_rank(value)) == bisect.bisect_right(
            keys, _rank(value)
        )
        assert self.sl.count(value) == self.model.count(value)
        if value in self.model:
            assert self.sl[self.sl.index(value)] == value
        else:
            with pytest.raises(ValueError, match=r"(?:is )?not in list"):
                self.sl.index(value)

    @rule(lo=BOUNDS, hi=BOUNDS)
    def irange_key(self, lo: int, hi: int) -> None:
        got = list(self.sl.irange_key(lo, hi))
        assert Counter(got) == Counter(v for v in self.model if lo <= _rank(v) <= hi)
        assert [_rank(v) for v in got] == sorted(map(_rank, got))

    @rule(values=st.lists(VALUES, max_size=4))
    def key_fault(self, values: list[int]) -> None:
        with pytest.raises(RefusedError) as raised:
            self.sl.add(99)
        assert raised.value is KEY_FAULT
        with pytest.raises(RefusedError) as raised:
            self.sl.update([*values, 99])
        assert raised.value is KEY_FAULT

    @invariant()
    def is_model_sorted_by_key(self) -> None:
        assert Counter(self.sl) == Counter(self.model)
        assert [_rank(v) for v in self.sl] == sorted(map(_rank, self.model))
        assert len(self.sl) == len(self.model)


class SortedDictMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.sd: SortedDict = SortedDict()
        self.model: dict[int, int] = {}

    @rule(key=VALUES, value=st.integers())
    def setitem(self, key: int, value: int) -> None:
        self.sd[key] = value
        self.model[key] = value

    @rule(key=VALUES)
    def delitem(self, key: int) -> None:
        if key in self.model:
            del self.sd[key]
            del self.model[key]
        else:
            with pytest.raises(KeyError):
                del self.sd[key]

    @rule(key=VALUES, default=st.integers())
    def pop_and_setdefault(self, key: int, default: int) -> None:
        assert self.sd.pop(key, default) == self.model.pop(key, default)
        assert self.sd.setdefault(key, default) == self.model.setdefault(key, default)

    @rule(pairs=st.dictionaries(VALUES, st.integers(), max_size=8))
    def update(self, pairs: dict[int, int]) -> None:
        self.sd.update(pairs)
        self.model.update(pairs)

    @precondition(lambda self: self.model)
    @rule(data=st.data())
    def popitem_and_peek(self, data: st.DataObject) -> None:
        keys = sorted(self.model)
        index = data.draw(st.integers(-len(keys), len(keys) - 1))
        expected = (keys[index], self.model[keys[index]])
        assert self.sd.peekitem(index) == expected
        assert self.sd.keys()[index] == keys[index]
        assert self.sd.popitem(index) == expected
        del self.model[keys[index]]

    @rule(key=VALUES, lo=BOUNDS, hi=BOUNDS)
    def rank_queries(self, key: int, lo: int, hi: int) -> None:
        keys = sorted(self.model)
        assert self.sd.bisect_left(key) == bisect.bisect_left(keys, key)
        assert self.sd.bisect_right(key) == bisect.bisect_right(keys, key)
        assert list(self.sd.irange(lo, hi)) == [k for k in keys if lo <= k <= hi]
        if key in self.model:
            assert self.sd.index(key) == keys.index(key)
        else:
            with pytest.raises(ValueError, match=r"(?:is )?not in list"):
                self.sd.index(key)

    @precondition(lambda self: self.model)
    @rule(key=VALUES, fail_at=st.integers(1, 6))
    def comparison_fault(self, key: int, fail_at: int) -> None:
        error = RefusedError()
        poison = Poison(key, fail_at, error)
        try:
            self.sd[poison] = -1
        except RefusedError as raised:
            assert raised is error  # noqa: PT017 — scheduled faults may succeed or raise.
        else:
            self.model[key] = -1
        poison.fail_at = 0

    @rule()
    def hash_fault(self) -> None:
        with pytest.raises(TypeError):
            self.sd[[0]] = 0  # type: ignore[index]

    @invariant()
    def matches_model(self) -> None:
        assert list(self.sd) == sorted(self.model)
        assert dict(self.sd) == self.model
        assert list(self.sd.values()) == [self.model[k] for k in sorted(self.model)]
        assert list(self.sd.items()) == sorted(self.model.items())


class SortedSetMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.ss: SortedSet = SortedSet()
        self.model: set[int] = set()

    @rule(value=VALUES)
    def add(self, value: int) -> None:
        self.ss.add(value)
        self.model.add(value)

    @rule(value=VALUES)
    def remove_or_discard(self, value: int) -> None:
        if value in self.model:
            self.ss.remove(value)
        else:
            with pytest.raises(KeyError):
                self.ss.remove(value)
        self.ss.discard(value)
        self.model.discard(value)

    @rule(values=st.sets(VALUES, max_size=12))
    def update(self, values: set[int]) -> None:
        self.ss.update(values)
        self.model |= values

    @rule(values=st.sets(VALUES, max_size=12))
    def difference_update(self, values: set[int]) -> None:
        self.ss.difference_update(values)
        self.model -= values

    @rule(values=st.sets(VALUES, max_size=12))
    def set_algebra(self, values: set[int]) -> None:
        for got, expected in (
            (self.ss & values, self.model & values),
            (self.ss | values, self.model | values),
            (self.ss - values, self.model - values),
            (self.ss ^ values, self.model ^ values),
        ):
            assert type(got) is SortedSet
            assert list(got) == sorted(expected)

    @precondition(lambda self: self.model)
    @rule(data=st.data())
    def pop(self, data: st.DataObject) -> None:
        ordered = sorted(self.model)
        index = data.draw(st.integers(-len(ordered), len(ordered) - 1))
        assert self.ss[index] == ordered[index]
        assert self.ss.index(ordered[index]) == index % len(ordered)
        assert self.ss.pop(index) == ordered[index]
        self.model.remove(ordered[index])

    @rule(value=VALUES)
    def bisect(self, value: int) -> None:
        ordered = sorted(self.model)
        assert self.ss.bisect_left(value) == bisect.bisect_left(ordered, value)
        assert self.ss.bisect_right(value) == bisect.bisect_right(ordered, value)

    @rule()
    def hash_fault(self) -> None:
        with pytest.raises(TypeError):
            self.ss.add([0])  # type: ignore[arg-type]

    @invariant()
    def matches_model(self) -> None:
        assert list(self.ss) == sorted(self.model)
        assert len(self.ss) == len(self.model)
        assert self.ss == self.model


@pytest.mark.parametrize(
    "machine",
    [SortedListMachine, SortedKeyListMachine, SortedDictMachine, SortedSetMachine],
    ids=lambda machine: machine.__name__,
)
def test_sorted_histories_match_their_reference_model(
    machine: type[RuleBasedStateMachine],
) -> None:
    require_backend("sortedcontainers")
    run_state_machine_as_test(machine)


def test_key_function_list_is_the_documented_key_variant() -> None:
    backend = require_backend("sortedcontainers")
    keyed = SortedList([3, 1, 2], key=lambda v: -v)
    assert isinstance(keyed, backend.SortedKeyList)
    assert isinstance(keyed, SortedList)
    assert type(SortedList([3, 1, 2])) is SortedList
    assert list(SortedList(["bb", "a", "ccc", "dd"], key=len)) in (
        ["a", "bb", "dd", "ccc"],
        ["a", "dd", "bb", "ccc"],
    )
