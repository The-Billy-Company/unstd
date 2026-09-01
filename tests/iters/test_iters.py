"""Adversarial tests for ``unstd.iters`` — the pure-stdlib combinator set.

These pin the contract the codebase leans on: the combinators are **lazy** (they
never pull more of the input than the caller asks for — proven with a generator
that detonates past a point, an infinite ``count()``, and a consumption spy),
``unique_everseen`` survives **unhashable keys**, ``chunked`` honors its uneven
tail and ``strict`` guard, ``partition`` walks the source **once**, and
``first``/``last``/``nth`` behave exactly on the empty/default edges.
"""

from __future__ import annotations

from itertools import count, islice
from typing import TYPE_CHECKING

import pytest

from unstd.iters import (
    batched_with_key,
    chunked,
    first,
    flatten,
    ilen,
    last,
    nth,
    partition,
    take,
    unique_everseen,
    unique_justseen,
    windowed,
)


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


# ── laziness instrumentation ───────────────────────────────────────────────────


class Spy[T]:
    """An iterator that records how many items were successfully pulled from it."""

    def __init__(self, seq: Iterable[T]) -> None:
        """Initialize the instance."""
        self._it = iter(seq)
        self.count = 0

    def __iter__(self) -> Iterator[T]:
        """Iterate the input."""
        return self

    def __next__(self) -> T:
        """Next."""
        value = next(self._it)  # StopIteration propagates without bumping the count
        self.count += 1
        return value


def raises_after(n: int) -> Iterator[int]:
    """Yield ``0..n-1`` then detonate — pulling the (n+1)-th item is a hard failure."""
    yield from range(n)
    msg = f"iterator consumed past {n} items — not lazy"
    raise AssertionError(msg)


# ── laziness: consume exactly as far as asked, never further ───────────────────


def test_first_consumes_exactly_one_item() -> None:
    """Test first consumes exactly one item."""
    spy = Spy(range(100))
    assert first(spy) == 0
    assert spy.count == 1
    assert first(raises_after(3)) == 0  # the detonator never fires


def test_nth_consumes_exactly_n_plus_one() -> None:
    """Test nth consumes exactly n plus one."""
    spy = Spy(range(100))
    assert nth(spy, 4) == 4
    assert spy.count == 5
    assert nth(raises_after(10), 2) == 2  # 3 pulled, well short of the boom


def test_take_consumes_exactly_n() -> None:
    """Test take consumes exactly n."""
    spy = Spy(range(100))
    assert take(3, spy) == [0, 1, 2]
    assert spy.count == 3
    assert take(3, count()) == [0, 1, 2]  # bounded prefix of an infinite source


def test_chunked_is_lazy_per_batch() -> None:
    """Test chunked is lazy per batch."""
    spy = Spy(range(100))
    it = chunked(spy, 2)
    assert next(it) == (0, 1)
    assert spy.count == 2  # only the first batch was pulled
    assert next(chunked(raises_after(5), 2)) == (0, 1)


def test_windowed_is_lazy_per_window() -> None:
    """Test windowed is lazy per window."""
    spy = Spy(range(100))
    it = windowed(spy, 3)
    assert next(it) == (0, 1, 2)
    assert spy.count == 3
    assert next(windowed(raises_after(3), 2)) == (0, 1)


def test_batched_with_key_is_lazy_across_groups() -> None:
    # An infinite source; pulling one group must not hang or over-read.
    """Test batched with key is lazy across groups."""
    groups = batched_with_key((i // 3 for i in count()), lambda x: x)
    assert next(groups) == (0, (0, 0, 0))


def test_flatten_is_lazy() -> None:
    """Test flatten is lazy."""
    assert take(3, flatten([i, i] for i in count())) == [0, 0, 1]


def test_unique_everseen_is_lazy() -> None:
    # First two uniques come from items 0,1 — the boom at 50 is never reached.
    """unique_everseen pulls only what the caller asks (Spy + detonator)."""
    spy = Spy(raises_after(50))
    assert list(islice(unique_everseen(spy), 2)) == [0, 1]
    assert spy.count == 2


def test_unique_justseen_is_lazy() -> None:
    """unique_justseen is lazy per yielded unique (Spy + detonator)."""
    spy = Spy(raises_after(50))
    assert list(islice(unique_justseen(spy), 3)) == [0, 1, 2]
    assert spy.count == 3


# ── unique_everseen: hardened against unhashable keys ──────────────────────────


def test_unique_everseen_hashable_path() -> None:
    """Test unique everseen hashable path."""
    assert list(unique_everseen([1, 2, 1, 3, 2, 3])) == [1, 2, 3]


def test_unique_everseen_unhashable_elements() -> None:
    # The elements themselves (lists) are unhashable — the set path raises
    # TypeError and the function must fall back to a linear seen-log, not crash.
    """Test unique everseen unhashable elements."""
    data = [[1], [2], [1], [3], [2]]
    assert list(unique_everseen(data)) == [[1], [2], [3]]


def test_unique_everseen_unhashable_key() -> None:
    """Test unique everseen unhashable key."""
    items = [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]
    # key returns a dict (unhashable) → still de-dupes by id, first wins.
    seen = list(unique_everseen(items, key=lambda d: {"id": d["id"]}))
    assert seen == [items[0], items[2]]


def test_unique_everseen_key_collapses_by_projection() -> None:
    """Test unique everseen key collapses by projection."""
    assert list(unique_everseen("AaBbAc", key=str.lower)) == ["A", "B", "c"]


# ── unique_justseen: only consecutive runs collapse ────────────────────────────


def test_unique_justseen_collapses_only_adjacent() -> None:
    """Test unique justseen collapses only adjacent."""
    assert list(unique_justseen([1, 1, 2, 2, 3, 1, 1])) == [1, 2, 3, 1]


def test_unique_justseen_with_key_and_unhashable_elements() -> None:
    """Test unique justseen with key and unhashable elements."""
    assert list(unique_justseen("AaBbCc", key=str.lower)) == ["A", "B", "C"]
    assert list(unique_justseen([[1], [1], [2], [1]])) == [[1], [2], [1]]


# ── chunked: uneven tail + strict mode ─────────────────────────────────────────


def test_chunked_uneven_tail_is_short_not_padded() -> None:
    """Test chunked uneven tail is short not padded."""
    assert list(chunked(range(7), 3)) == [(0, 1, 2), (3, 4, 5), (6,)]


def test_chunked_exact_multiple() -> None:
    """Test chunked exact multiple."""
    assert list(chunked(range(6), 3)) == [(0, 1, 2), (3, 4, 5)]


def test_chunked_strict_raises_on_ragged_tail() -> None:
    """Test chunked strict raises on ragged tail."""
    with pytest.raises(ValueError, match="incomplete batch"):
        list(chunked(range(7), 3, strict=True))


def test_chunked_strict_passes_on_exact_multiple() -> None:
    """Test chunked strict passes on exact multiple."""
    assert list(chunked(range(6), 3, strict=True)) == [(0, 1, 2), (3, 4, 5)]


def test_chunked_rejects_non_positive_n() -> None:
    """Test chunked rejects non positive n."""
    with pytest.raises(ValueError, match="n must be >= 1"):
        chunked([1, 2], 0)


# ── windowed: overlap, stride, boundaries ──────────────────────────────────────


def test_windowed_basic_overlap() -> None:
    """Test windowed basic overlap."""
    assert list(windowed([1, 2, 3, 4], 2)) == [(1, 2), (2, 3), (3, 4)]


def test_windowed_step_no_overlap() -> None:
    """Test windowed step no overlap."""
    assert list(windowed(range(5), 2, step=2)) == [(0, 1), (2, 3)]


def test_windowed_step_larger_than_window_skips_gap() -> None:
    """Test windowed step larger than window skips gap."""
    assert list(windowed(range(7), 2, step=3)) == [(0, 1), (3, 4)]


def test_windowed_drops_incomplete_tail() -> None:
    """Test windowed drops incomplete tail."""
    assert list(windowed([1, 2], 3)) == []


def test_windowed_zero_yields_single_empty_tuple() -> None:
    """Test windowed zero yields single empty tuple."""
    assert list(windowed([1, 2, 3], 0)) == [()]


def test_windowed_rejects_bad_args() -> None:
    """Test windowed rejects bad args."""
    with pytest.raises(ValueError, match="n must be >= 0"):
        list(windowed([1, 2], -1))
    with pytest.raises(ValueError, match="step must be >= 1"):
        list(windowed([1, 2], 2, step=0))


# ── batched_with_key: run-length grouping, transient-group-safe ────────────────


def test_batched_with_key_materializes_each_run() -> None:
    """Test batched with key materializes each run."""
    groups = list(batched_with_key([1, 1, 2, 3, 3, 3, 1], lambda x: x))
    assert groups == [(1, (1, 1)), (2, (2,)), (3, (3, 3, 3)), (1, (1,))]


def test_batched_with_key_projects_the_key() -> None:
    """Test batched with key projects the key."""
    words = ["at", "an", "be", "by", "cat"]
    grouped = [(k, len(run)) for k, run in batched_with_key(words, lambda w: w[0])]
    assert grouped == [("a", 2), ("b", 2), ("c", 1)]


# ── partition: one shared pass, no double-consume ──────────────────────────────


def test_partition_splits_false_then_true() -> None:
    """Test partition splits false then true."""
    falsy, truthy = partition(lambda n: n % 2, range(6))
    assert list(falsy) == [0, 2, 4]
    assert list(truthy) == [1, 3, 5]


def test_partition_consumes_source_exactly_once() -> None:
    """Test partition consumes source exactly once."""
    spy = Spy(range(6))
    falsy, truthy = partition(lambda n: n % 2, spy)
    assert spy.count == 0  # nothing read until a side is pulled (lazy)
    assert list(falsy) == [0, 2, 4]
    assert list(truthy) == [1, 3, 5]
    assert spy.count == 6  # each source item drawn once — never twice


def test_partition_is_lazy_on_infinite_source() -> None:
    """Test partition is lazy on infinite source."""
    _, truthy = partition(lambda n: n % 2, count())
    assert take(3, truthy) == [1, 3, 5]


# ── first / last / nth: empty + default edges ──────────────────────────────────


def test_first_defaults_and_empty() -> None:
    """Test first defaults and empty."""
    assert first([5, 6]) == 5
    assert first([], default=None) is None
    assert first([], default="x") == "x"
    with pytest.raises(ValueError, match="empty iterable"):
        first([])


def test_last_defaults_and_empty() -> None:
    """Test last defaults and empty."""
    assert last([1, 2, 3]) == 3
    assert last(iter([1, 2, 3])) == 3  # non-reversible → deque drain path
    assert last(range(1_000_000)) == 999_999  # reversible → O(1) tail
    assert last([], default=9) == 9
    assert last(iter([]), default=7) == 7
    with pytest.raises(ValueError, match="empty iterable"):
        last([])


def test_nth_defaults_and_out_of_range() -> None:
    """Test nth defaults and out of range."""
    assert nth(range(10), 3) == 3
    assert nth([1, 2], 5, default="z") == "z"
    with pytest.raises(ValueError, match="out of range"):
        nth([1, 2], 5)
    with pytest.raises(ValueError, match="n must be >= 0"):
        nth([1, 2], -1)


# ── ilen / flatten ─────────────────────────────────────────────────────────────


def test_ilen_counts_and_exhausts() -> None:
    """Test ilen counts and exhausts."""
    assert ilen(range(2000)) == 2000
    assert ilen(iter([])) == 0
    spy = Spy(range(5))
    assert ilen(spy) == 5
    assert spy.count == 5
    assert list(spy) == []  # fully consumed afterwards


def test_flatten_one_level_only() -> None:
    """Test flatten one level only."""
    assert list(flatten([[1, 2], [3], [4, 5]])) == [1, 2, 3, 4, 5]
    assert list(flatten([[[1]], [[2]]])) == [[1], [2]]  # only one level removed
    assert list(flatten(["ab", "cd"])) == ["a", "b", "c", "d"]  # strings are iterables
