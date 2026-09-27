"""Consecutive-run combinators — fixed-size chunks, sliding windows, run-length grouping.

The three "reshape a flat stream into consecutive groups" idioms. Each is lazy
and native over stdlib (:func:`itertools.batched` on 3.13, :func:`itertools.groupby`,
:class:`collections.deque`) — never a third-party dependency.

Same-named siblings of the ``more-itertools`` helpers (Bettini et al.,
https://github.com/more-itertools/more-itertools) and the official ``itertools``
recipes section — frozen against more-itertools' pre-11.0 shape, not an evergreen
parity claim. :func:`windowed` in particular is a deliberately *different*
function from more-itertools' current (``>=11.0``) one: this one drops any
incomplete trailing window and treats ``n == 0`` as a valid "yield one empty
tuple" edge case, where more-itertools 11.0+ pads an incomplete tail with
``fillvalue`` and raises ``ValueError`` on ``n <= 0``. Both are internally
consistent; they are not interchangeable.
"""

from __future__ import annotations

from collections import deque
from itertools import batched, groupby, islice, tee
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


__all__ = ["chunked", "runs", "windowed"]


def chunked[T](
    iterable: Iterable[T], n: int, *, strict: bool = False
) -> Iterator[tuple[T, ...]]:
    """Break *iterable* into consecutive *n*-length tuples (the final one may be short).

    A named, validated superset of stdlib :func:`itertools.batched`: same lazy,
    one-batch-at-a-time behavior, but *n* is range-checked up front and
    ``strict`` works uniformly from Python 3.12 on — :func:`itertools.batched`
    only grew its own ``strict`` keyword in 3.13 (`bpo-gh-113202`). With
    ``strict=True`` an uneven final batch raises ``ValueError`` — reach for it
    when a ragged tail signals a bug (a mis-sized record stream). Yields
    ``tuple``s, so a chunk is hashable and cheap.
    """
    if n < 1:
        msg = "n must be >= 1"
        raise ValueError(msg)
    return _chunked(iterable, n, strict=strict)


def _chunked[T](
    iterable: Iterable[T], n: int, *, strict: bool
) -> Iterator[tuple[T, ...]]:
    # `batched` only ever shorts its *last* batch, so a short batch seen here
    # is necessarily final — no lookahead needed to match its own strict timing.
    for batch in batched(iterable, n):
        if strict and len(batch) != n:
            msg = "batched(): incomplete batch"
            raise ValueError(msg)
        yield batch


def windowed[T](
    iterable: Iterable[T], n: int, step: int = 1
) -> Iterator[tuple[T, ...]]:
    """Yield overlapping length-*n* tuples sliding across *iterable*, advancing *step* each time.

    Only **complete** windows are emitted — a trailing remainder shorter than *n*,
    or one that cannot advance a full *step*, is dropped — so ``windowed(seq, 2)``
    is a size/stride-tunable superset of stdlib :func:`itertools.pairwise`. Lazy:
    holds at most *n* items in a ``deque`` regardless of input length. ``n == 0``
    yields a single empty tuple — a deliberate divergence from current
    ``more-itertools`` (``>=11.0``), which raises ``ValueError`` on ``n <= 0`` and,
    for a nonzero ``n``, pads an incomplete tail with ``fillvalue`` instead of
    dropping it; there is no ``fillvalue`` parameter here.
    """
    if n < 0:
        msg = "n must be >= 0"
        raise ValueError(msg)
    if step < 1:
        msg = "step must be >= 1"
        raise ValueError(msg)
    if n == 0:
        return iter(((),))
    if step == 1:
        # The `pairwise` shape generalized: n staggered `tee` views zipped together
        # is a C-level walk, ~5x a Python deque loop, and `zip` stopping at the
        # shortest view is exactly "complete windows only".
        views = tee(iterable, n)
        for lag, view in enumerate(views):
            next(islice(view, lag, lag), None)
        return zip(*views, strict=False)
    return _strided(iter(iterable), n, step)


def _strided[T](it: Iterator[T], n: int, step: int) -> Iterator[tuple[T, ...]]:
    window: deque[T] = deque(islice(it, n), maxlen=n)
    if len(window) == n:
        yield tuple(window)
    while True:
        advanced = 0
        for item in islice(it, step):
            window.append(item)
            advanced += 1
        if advanced < step:  # ran out mid-stride → no more complete windows
            return
        yield tuple(window)


def runs[T, K](
    iterable: Iterable[T], key: Callable[[T], K]
) -> Iterator[tuple[K, tuple[T, ...]]]:
    """Group *consecutive* items sharing a *key* into ``(key, run)`` pairs.

    Run-length grouping — a per-group-eager wrapper over :func:`itertools.groupby`
    that materializes each run into a ``tuple`` (groupby's sub-iterators are shared
    and invalidate the moment the outer iterator advances — the classic footgun).
    Lazy *across* groups: the next run is not read until requested. Only *adjacent*
    equal-keyed items merge, so sort by *key* first if you want global buckets.
    """
    for k, grp in groupby(iterable, key):
        yield k, tuple(grp)
