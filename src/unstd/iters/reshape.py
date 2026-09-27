"""Restructuring combinators — one-pass predicate split, and one-level flatten.

``partition`` splits a stream into its false/true halves in a single walk;
``flatten`` concatenates one level of nesting. Both lazy, both native over
stdlib (:func:`itertools.tee`, :func:`itertools.chain`) — never a dependency.
Native reimplementations of the official ``itertools`` recipes / same-named
``more-itertools`` helpers (Bettini et al.,
https://github.com/more-itertools/more-itertools).
"""

from __future__ import annotations

from itertools import chain, filterfalse, tee
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


__all__ = ["flatten", "partition"]


def partition[T](
    pred: Callable[[T], object], iterable: Iterable[T]
) -> tuple[Iterator[T], Iterator[T]]:
    """Split *iterable* into ``(falsy, truthy)`` iterators by *pred*, over one shared pass.

    The ``itertools`` recipe: :func:`itertools.tee` shares a *single* underlying
    walk of *iterable* between the two returned iterators, so the source is **never
    consumed twice** — each element is drawn from *iterable* exactly once and
    buffered only until both sides have seen it. Order-preserving within each side,
    and fully lazy (nothing is read until a returned iterator is pulled).

    ``pred`` is evaluated once per element per side (tee duplicates the values, not
    the predicate). ``tee``'s internal buffer grows if one side is drained far
    ahead of the other — pull the two roughly in step for bounded memory. This is
    the classic recipe shape; ``more-itertools`` ``>=11.0`` rewrote its own
    ``partition`` onto a two-``deque`` generator for better behavior under a
    lopsided pull — same output either way, just a different memory profile under
    that specific access pattern.
    """
    t1, t2 = tee(iterable)
    return filterfalse(pred, t1), filter(pred, t2)


flatten = chain.from_iterable
"""Flatten exactly one level of nesting — ``itertools.chain.from_iterable`` renamed.

Concatenates the sub-iterables lazily into one stream; only a single level is
removed (a list of lists of lists stays a list of lists inside). Named because
``chain.from_iterable`` is the least-discoverable of the hot ``itertools``
idioms. Note strings are iterables of characters: ``flatten(["ab", "cd"])``
yields ``'a', 'b', 'c', 'd'``.
"""
