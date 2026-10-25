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
from itertools import batched, chain, groupby, islice, pairwise, tee
from operator import index
from sys import maxsize
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
    if strict is False:
        yield from batched(iterable, n)
        return
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
    uses O(*n*) storage in stdlib iterators or a ``deque``. ``n == 0``
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
        # Staggering reads n - 1 items, so `chain` defers it to the first pull
        # while keeping the walk C-level.
        return chain.from_iterable(map(_staggered, (iterable,), (n,)))
    return _strided(iter(iterable), n, step)


def _staggered[T](iterable: Iterable[T], n: int) -> Iterator[tuple[T, ...]]:
    n = index(n)
    if n > maxsize:
        msg = "Python int too large to convert to C ssize_t"
        raise OverflowError(msg)
    if n == 2:
        return pairwise(iterable)
    # The `pairwise` shape generalized: n staggered `tee` views zipped together
    # keep the walk in C; stopping at the shortest means complete windows only.
    # Fork the advancing view at each offset rather than walking 0 + 1 + ... +
    # (n - 1) positions through independent views: startup is linear in n.
    (view,) = tee(iterable, 1)
    views = []
    for _ in range(n - 1):
        previous, view = tee(view)
        views.append(previous)
        next(view, None)
    views.append(view)
    return zip(*views, strict=False)


def _strided[T](it: Iterator[T], n: int, step: int) -> Iterator[tuple[T, ...]]:
    window: deque[T] = deque(islice(it, n), maxlen=n)
    if len(window) == n:
        yield tuple(window)
    if type(step) is not int or step > maxsize:
        # Preserve islice's index conversion and error timing for custom sizes.
        while True:
            advanced = 0
            for item in islice(it, step):
                window.append(item)
                advanced += 1
            if advanced < step:
                return
            yield tuple(window)
    while True:
        window.extend(islice(it, step - 1))
        try:
            item = next(it)
        except StopIteration:  # ran out mid-stride → no more complete windows
            return
        window.append(item)
        del item
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
