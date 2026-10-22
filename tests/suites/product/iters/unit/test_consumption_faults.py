"""Size/stride boundaries, predicate faults, uneven partition pulls and retention.

Expected values are hand-derived tables or plain-loop consequences of the
documented contract. Retention is observed through weakly referenced tokens made
on demand by the source, so a live count is exactly what the combinator (plus
the test's own references) still holds.
"""

from __future__ import annotations

from itertools import count, islice
from operator import itemgetter
from typing import TYPE_CHECKING
import weakref

import pytest

from unstd.iters import (
    chunked,
    first,
    ilen,
    last,
    nth,
    partition,
    runs,
    take,
    unique_everseen,
    unique_justseen,
    windowed,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class Untouchable:
    """A source whose first pull is a test failure."""

    def __iter__(self) -> Untouchable:
        return self

    def __next__(self) -> int:
        pytest.fail("argument validation read the source")


class Token:
    __slots__ = ("__weakref__", "value")

    def __init__(self, value: int) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Token) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)


class Tokens:
    """Mints tokens lazily and records how many were alive at each mint."""

    def __init__(self, values: Iterator[int]) -> None:
        self.live: weakref.WeakSet[Token] = weakref.WeakSet()
        self.peaks: list[int] = []
        self._values = values

    def __iter__(self) -> Iterator[Token]:
        for value in self._values:
            token = Token(value)
            self.live.add(token)
            self.peaks.append(len(self.live))
            yield token
            del token


# ── sizes and strides ──────────────────────────────────────────────────────────

FIVE = (0, 1, 2, 3, 4)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, [(0,), (1,), (2,), (3,), (4,)]),
        (4, [(0, 1, 2, 3), (4,)]),
        (5, [FIVE]),
        (6, [FIVE]),
    ],
)
def test_chunked_size_boundaries(n: int, expected: list[tuple[int, ...]]) -> None:
    assert list(chunked(FIVE, n)) == expected
    assert list(chunked(iter(FIVE), n)) == expected
    assert list(chunked((), n)) == []


@pytest.mark.parametrize(
    ("n", "complete", "ragged"),
    [
        (1, [(0,), (1,), (2,), (3,), (4,)], False),
        (2, [(0, 1), (2, 3)], True),
        (4, [(0, 1, 2, 3)], True),
        (5, [FIVE], False),
        (6, [], True),
    ],
)
def test_chunked_strict_yields_complete_batches_then_rejects_a_ragged_tail(
    n: int, complete: list[tuple[int, ...]], ragged: bool
) -> None:
    produced = chunked(iter(FIVE), n, strict=True)
    assert list(islice(produced, len(complete))) == complete
    if ragged:
        with pytest.raises(ValueError, match="incomplete batch"):
            next(produced)
    assert next(produced, None) is None


@pytest.mark.parametrize(
    ("n", "step", "expected"),
    [
        (0, 1, [()]),
        (0, 7, [()]),
        (1, 1, [(0,), (1,), (2,), (3,), (4,)]),
        (1, 2, [(0,), (2,), (4,)]),
        (4, 1, [(0, 1, 2, 3), (1, 2, 3, 4)]),
        (4, 4, [(0, 1, 2, 3)]),
        (4, 5, [(0, 1, 2, 3)]),
        (5, 1, [FIVE]),
        (5, 6, [FIVE]),
        (6, 1, []),
        (6, 2, []),
        (2, 3, [(0, 1), (3, 4)]),
        (2, 4, [(0, 1)]),
        (2, 6, [(0, 1)]),
    ],
)
def test_windowed_size_and_stride_boundaries(
    n: int, step: int, expected: list[tuple[int, ...]]
) -> None:
    assert list(windowed(FIVE, n, step)) == expected
    assert list(windowed(iter(FIVE), n, step)) == expected


@pytest.mark.parametrize(
    "call",
    [
        lambda source: chunked(source, 0),
        lambda source: chunked(source, -3),
        lambda source: list(windowed(source, -1)),
        lambda source: list(windowed(source, 2, 0)),
        lambda source: list(windowed(source, 2, -2)),
        lambda source: take(source, -1),
        lambda source: nth(source, -1, None),
    ],
)
def test_invalid_sizes_raise_before_touching_the_source(
    call: Callable[[Untouchable], object],
) -> None:
    with pytest.raises(ValueError, match=">= "):
        call(Untouchable())


def test_zero_sizes_read_nothing() -> None:
    assert take(Untouchable(), 0) == []
    assert list(windowed(Untouchable(), 0, 3)) == [()]


# ── predicate and key faults ───────────────────────────────────────────────────


class RefusedError(Exception):
    pass


def refusing_at(
    position: int, error: RefusedError, project: Callable[[int], object]
) -> Callable[[int], object]:
    calls = count()

    def key(value: int) -> object:
        if next(calls) == position:
            raise error
        return project(value)

    return key


DATA = [3, 3, 1, 4, 4, 4, 1, 5, 9, 9]


def _parity(value: int) -> int:
    return value % 2


def _same(value: int) -> int:
    return value


@pytest.mark.parametrize(
    ("combinator", "project", "expected"),
    [
        (runs, _parity, [(1, (3, 3, 1))]),
        (unique_justseen, _same, [3, 1, 4]),
        (unique_everseen, _same, [3, 1, 4]),
        (lambda xs, key: partition(xs, key)[0], _parity, [4, 4]),
    ],
    ids=["runs", "unique_justseen", "unique_everseen", "partition"],
)
def test_key_fault_propagates_after_the_exact_reference_prefix(
    combinator: Callable[[list[int], Callable[[int], object]], Iterator[object]],
    project: Callable[[int], object],
    expected: list[object],
) -> None:
    # The sixth key call refuses, i.e. while DATA[5] is being read.
    error = RefusedError()
    produced = combinator(DATA, refusing_at(5, error, project))
    assert list(islice(produced, len(expected))) == expected
    with pytest.raises(RefusedError) as raised:
        next(produced)
    assert raised.value is error


def test_partition_predicate_fault_leaves_the_other_side_its_buffered_prefix() -> None:
    error = RefusedError()
    falsy, truthy = partition(DATA, refusing_at(7, error, _parity))
    assert list(islice(truthy, 4)) == [3, 3, 1, 1]
    with pytest.raises(RefusedError) as raised:
        next(truthy)
    assert raised.value is error
    assert list(islice(falsy, 3)) == [4, 4, 4]


# ── uneven partition consumption ───────────────────────────────────────────────


@pytest.mark.parametrize("order", ["falsy", "truthy", "interleaved"])
def test_partition_sides_drain_in_either_order_or_interleaved(order: str) -> None:
    source = list(range(20))
    falsy, truthy = partition(source, lambda v: v % 3 == 0)
    if order == "falsy":
        rejected = list(falsy)
        accepted = list(truthy)
    elif order == "truthy":
        accepted = list(truthy)
        rejected = list(falsy)
    else:
        rejected, accepted = [], []
        for _ in range(20):
            rejected += islice(falsy, 2)
            accepted += islice(truthy, 1)
    assert rejected == [1, 2, 4, 5, 7, 8, 10, 11, 13, 14, 16, 17, 19]
    assert accepted == [0, 3, 6, 9, 12, 15, 18]


def test_partition_buffers_every_item_while_one_side_is_untouched() -> None:
    tokens = Tokens(iter(range(300)))
    falsy, truthy = partition(tokens, lambda t: t.value % 2)
    assert sum(1 for _ in falsy) == 150
    assert len(tokens.live) == 300
    assert [t.value for t in truthy] == list(range(1, 300, 2))
    del falsy, truthy
    assert len(tokens.live) == 0


def _in_step_peak(length: int) -> int:
    tokens = Tokens(iter(range(length)))
    falsy, truthy = partition(tokens, lambda t: t.value % 2)
    while next(falsy, None) is not None and next(truthy, None) is not None:
        pass
    del falsy, truthy
    assert len(tokens.live) == 0
    return max(tokens.peaks)


def test_partition_pulled_in_step_holds_a_length_independent_buffer() -> None:
    assert _in_step_peak(4000) == _in_step_peak(600) < 600


# ── retained objects ───────────────────────────────────────────────────────────


def _windowed_peak(length: int, n: int, step: int) -> int:
    tokens = Tokens(iter(range(length)))
    assert set(map(len, windowed(tokens, n, step))) == {n}
    assert len(tokens.live) == 0
    return max(tokens.peaks)


@pytest.mark.parametrize("step", [1, 2, 5])
def test_windowed_retention_does_not_grow_with_input_length(step: int) -> None:
    assert _windowed_peak(5000, 4, step) == _windowed_peak(800, 4, step) < 800


def test_strided_windowed_holds_one_window_plus_the_incoming_item() -> None:
    assert _windowed_peak(500, 4, 3) == 4 + 1


def _chunked_peak(length: int) -> int:
    tokens = Tokens(iter(range(length)))
    assert sum(map(len, chunked(tokens, 7))) == length
    assert len(tokens.live) == 0
    return max(tokens.peaks)


def test_chunked_holds_at_most_the_yielded_batch_and_the_one_being_built() -> None:
    assert _chunked_peak(2000) == _chunked_peak(140) <= 2 * 7


# A final new value keeps the generator suspended with its seen history alive.
REPEATS = [*(v % 10 for v in range(200)), 10]


def test_unique_everseen_retains_first_occurrences_and_releases_repeats() -> None:
    tokens = Tokens(iter(REPEATS))
    produced = unique_everseen(tokens)
    assert [t.value for t in islice(produced, 11)] == list(range(11))
    assert sorted(t.value for t in tokens.live) == list(range(11))


def test_keyed_unique_everseen_retains_keys_not_elements() -> None:
    tokens = Tokens(iter(REPEATS))
    produced = unique_everseen(tokens, key=lambda t: t.value)
    assert [t.value for t in islice(produced, 11)] == list(range(11))
    assert [t.value for t in tokens.live] == [10]


@pytest.mark.parametrize(
    "consume",
    [
        lambda xs: sum(map(len, map(itemgetter(1), runs(xs, lambda t: t.value // 5)))),
        lambda xs: sum(1 for _ in unique_justseen(xs, key=lambda t: t.value // 5)),
        ilen,
        lambda xs: last(xs).value,
    ],
    ids=["runs", "unique_justseen", "ilen", "last"],
)
def test_draining_combinators_hold_a_length_independent_number_of_items(
    consume: Callable[[Tokens], object],
) -> None:
    small, large = Tokens(iter(range(100))), Tokens(iter(range(2000)))
    consume(small)
    consume(large)
    assert max(large.peaks) == max(small.peaks) <= 5 + 2
    assert len(large.live) == 0


def test_selectors_never_mint_beyond_what_they_return() -> None:
    tokens = Tokens(count())
    assert first(tokens).value == 0
    assert nth(tokens, 2).value == 3
    assert [t.value for t in take(tokens, 2)] == [4, 5]
    assert len(tokens.peaks) == 6
    assert len(tokens.live) == 0
