"""Order-preserving de-duplication — drop repeats globally, or collapse runs.

``unique_everseen`` remembers everything it has yielded and drops any later
repeat; ``unique_justseen`` only collapses *consecutive* duplicates. Both are
lazy, order-preserving, native reimplementations of the official ``itertools``
recipes / same-named ``more-itertools`` helpers (Bettini et al.,
https://github.com/more-itertools/more-itertools) — never a dependency.
"""

from __future__ import annotations

from itertools import groupby
from operator import itemgetter
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


__all__ = ["unique_everseen", "unique_justseen"]


def _remember_unhashable(
    candidate: object,
    seen: set[object],
    unhashable: list[object],
    comparison_error: TypeError,
) -> bool:
    """Remember an unhashable first occurrence or preserve a comparison error."""
    try:
        hash(candidate)
    except TypeError:
        pass
    else:
        raise comparison_error
    if candidate in unhashable or any(
        previous is candidate or previous == candidate for previous in seen
    ):
        return False
    unhashable.append(candidate)
    return True


def unique_everseen[T](
    iterable: Iterable[T], key: Callable[[T], object] | None = None
) -> Iterator[T]:
    """Yield items once each — first occurrence wins — across the whole stream, lazily.

    The ``itertools`` recipe, hardened for **unhashable keys**: hashable keys ride
    a ``set`` (O(1) membership), while the first unhashable key transparently
    switches *that* item onto a ``list`` seen-log (O(n) membership) so a key like a
    ``dict`` or ``list`` still de-dupes correctly instead of raising ``TypeError``.
    Equality is checked across both histories, so equal hashable/unhashable keys
    (for example ``frozenset({1})`` and ``{1}``) share one first occurrence.
    Exceptions raised by hashable keys' comparisons propagate to the caller.
    Lazy — reads only as far as the caller pulls. Deliberately diverges from
    ``more-itertools`` ``>=11.0``, which removed its own silent unhashable-key
    fallback and now raises ``TypeError`` there (recommending ``key=tuple``/
    ``key=frozenset`` at the call site instead) — this module keeps the fallback
    as a usability improvement, not an oversight.
    """
    seen: set[object] = set()
    remember = seen.add
    unhashable: list[object] = []
    if key is None:
        # Until the first unhashable item there is only one history. Keep this
        # common prefix free of projection and mixed-history checks, then resume
        # the shared mixed-key loop with the same iterator and histories.
        iterable = iter(iterable)
        for element in iterable:
            try:
                if element in seen:
                    continue
                remember(element)
            except TypeError as error:
                first_unhashable = _remember_unhashable(
                    element, seen, unhashable, error
                )
                break
            yield element
        else:
            return
        if first_unhashable:
            yield element
    else:
        iterable = iter(iterable)
        for element in iterable:
            candidate = key(element)
            try:
                if candidate in seen:
                    continue
                remember(candidate)
            except TypeError as error:
                first_unhashable = _remember_unhashable(
                    candidate, seen, unhashable, error
                )
                break
            yield element
        else:
            return
        if first_unhashable:
            yield element
    for element in iterable:
        candidate = element if key is None else key(element)
        try:
            if candidate in seen or candidate in unhashable:
                continue
            remember(candidate)
        except TypeError as error:
            if not _remember_unhashable(candidate, seen, unhashable, error):
                continue
        yield element


def unique_justseen[T](
    iterable: Iterable[T], key: Callable[[T], object] | None = None
) -> Iterator[T]:
    """Yield items dropping only *consecutive* duplicates (collapse equal runs), lazily.

    ``groupby``-based (``more-itertools.unique_justseen``): O(1) memory — it never
    builds a seen-set, so unlike :func:`unique_everseen` it works even when keys
    are unhashable. Emits the first element of each consecutive run.
    """
    return map(next, map(itemgetter(1), groupby(iterable, key)))
