"""Restructuring combinators — one-pass predicate split, and one-level flatten.

``partition`` splits a stream into its false/true halves in a single walk;
``flatten`` concatenates one level of nesting. Both lazy, both native over
stdlib (:func:`itertools.tee`, :func:`itertools.chain`) — never a dependency.
Native reimplementations of the official ``itertools`` recipes / same-named
``more-itertools`` helpers (Bettini et al.,
https://github.com/more-itertools/more-itertools).
"""

from __future__ import annotations

from itertools import chain, compress, tee
from operator import not_
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


__all__ = ["flatten", "partition"]


def partition[T](
    iterable: Iterable[T], pred: Callable[[T], object]
) -> tuple[Iterator[T], Iterator[T]]:
    """Split *iterable* into ``(falsy, truthy)`` iterators by *pred*, over one shared pass.

    Iterable first, like every other combinator here. The current ``itertools``
    recipe: :func:`itertools.tee` shares a *single* walk of *iterable* between
    both sides, and the verdicts are teed alongside it, so each element is drawn
    **once** and *pred* runs **once** per element (not once per side — a
    side-effecting or expensive predicate is safe). Order-preserving within each
    side, fully lazy, and a C-level walk end to end. ``tee`` buffers whatever one
    side has read ahead of the other — pull the two roughly in step for bounded
    memory.
    """
    t1, t2, p = tee(iterable, 3)
    p1, p2 = tee(map(pred, p))
    return compress(t1, map(not_, p1)), compress(t2, p2)


flatten = chain.from_iterable
"""Flatten exactly one level of nesting — ``itertools.chain.from_iterable`` renamed.

Concatenates the sub-iterables lazily into one stream; only a single level is
removed (a list of lists of lists stays a list of lists inside). Named because
``chain.from_iterable`` is the least-discoverable of the hot ``itertools``
idioms. Note strings are iterables of characters: ``flatten(["ab", "cd"])``
yields ``'a', 'b', 'c', 'd'``.
"""
