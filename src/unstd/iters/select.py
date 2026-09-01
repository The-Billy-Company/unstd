"""Positional selection & counting over an arbitrary iterable.

The ``next(iter(x), default)`` peek, the "give me item N", and the "count a
generator without building a list" idioms — fused into named, total helpers the
stdlib never shipped as single calls. Every function is thin over a stdlib
primitive (:func:`itertools.islice`, :class:`collections.deque`, :func:`reversed`).

Native reimplementations of the same-named ``more-itertools`` helpers (Bettini et
al., https://github.com/more-itertools/more-itertools) and the official
``itertools`` recipes section — **not** a dependency on either.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Reversible
from enum import Enum
from itertools import count, islice
from typing import cast, overload


__all__ = ["first", "ilen", "last", "nth", "take"]


class _Missing(Enum):
    """Private no-default sentinel — distinct from ``None`` so ``None`` is a valid default. A single-member enum so type checkers narrow ``is _MISSING`` checks."""

    TOKEN = 0

    def __repr__(self) -> str:
        return "<no default>"


_MISSING = _Missing.TOKEN


@overload
def first[T](iterable: Iterable[T]) -> T: ...
@overload
def first[T, D](iterable: Iterable[T], default: D) -> T | D: ...
def first[T, D](iterable: Iterable[T], default: D | _Missing = _MISSING) -> T | D:
    """Return the first item of *iterable*, or *default* if it is empty.

    Consumes exactly one item — never walks the rest — so it is O(1) and safe on
    an infinite or expensive generator. Raises ``ValueError`` on empty input when
    no *default* is given (matching ``more-itertools.first``).
    """
    for item in iterable:
        return item
    if default is _MISSING:
        msg = "first() called on an empty iterable and no default given"
        raise ValueError(msg)
    return default


@overload
def last[T](iterable: Iterable[T]) -> T: ...
@overload
def last[T, D](iterable: Iterable[T], default: D) -> T | D: ...
def last[T, D](iterable: Iterable[T], default: D | _Missing = _MISSING) -> T | D:
    """Return the last item of *iterable*, or *default* if it is empty.

    Uses O(1) ``reversed`` for sequences; for a one-shot iterable it drains the
    stream through a ``maxlen=1`` deque, holding only the trailing item (never the
    whole input). Raises ``ValueError`` on empty input when no *default* is given.
    """
    if isinstance(iterable, Reversible):
        # isinstance cannot carry the parameter, so it narrows to a bare
        # `Reversible` and erases the element type the caller passed in; the cast
        # restores exactly what the `Iterable[T]` annotation already guaranteed.
        for item in reversed(cast("Reversible[T]", iterable)):  # O(1) tail
            return item
    elif tail := deque(iterable, maxlen=1):  # not reversible → drain, keep the last
        return tail[0]
    if default is _MISSING:
        msg = "last() called on an empty iterable and no default given"
        raise ValueError(msg)
    return default


@overload
def nth[T](iterable: Iterable[T], n: int) -> T: ...
@overload
def nth[T, D](iterable: Iterable[T], n: int, default: D) -> T | D: ...
def nth[T, D](iterable: Iterable[T], n: int, default: D | _Missing = _MISSING) -> T | D:
    """Return the item at 0-based index *n*, or *default* if the iterable is shorter.

    Consumes at most ``n + 1`` items (``islice`` then ``next``) — cheap on a long
    stream. Raises ``ValueError`` for negative *n*, and (when no *default* is
    given) ``ValueError`` if the index is out of range.
    """
    if n < 0:
        msg = "n must be >= 0"
        raise ValueError(msg)
    item = next(islice(iter(iterable), n, None), _MISSING)
    if item is _MISSING:
        if default is _MISSING:
            msg = f"nth() index {n} is out of range and no default given"
            raise ValueError(msg)
        return default
    return item


def take[T](n: int, iterable: Iterable[T]) -> list[T]:
    """Return the first *n* items of *iterable* as a ``list`` (``list(islice(...))``).

    Materializes by design — a bounded prefix — so the name says ``list``, not
    iterator. Fewer than *n* items available yields a shorter list (no error).
    """
    if n < 0:
        msg = "n must be >= 0"
        raise ValueError(msg)
    return list(islice(iterable, n))


def ilen(iterable: Iterable[object]) -> int:
    """Count the items in *iterable*, consuming it entirely, without building a list.

    Uses the ``deque(zip(it, count()), maxlen=0)`` drain idiom (the fast
    ``itertools`` recipe): ``zip`` advances the counter once per item while the
    zero-length deque discards the pairs, so counting a huge generator stays O(1)
    in memory. **The iterable is exhausted afterwards.**
    """
    counter = count()
    deque(zip(iterable, counter), maxlen=0)  # noqa: B905 — counter is infinite; no strict pairing
    return next(counter)
