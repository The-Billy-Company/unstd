"""Always-sorted collections with optional native positional operations.

The stdlib gives you ``bisect`` over a hand-maintained list, but no container
that *keeps itself* sorted through inserts and deletions. This module exposes the
three that fill that gap:

- :class:`SortedDict` — a ``dict`` whose keys iterate in sorted order.
- :class:`SortedList` — a mutable sequence kept sorted; ``O(log n)`` membership,
  rank, and range queries via ``bisect``/``irange``.
- :class:`SortedSet` — a ``set`` with sorted iteration and indexable ordering.

Backend: these come from `sortedcontainers
<https://grantjenks.com/docs/sortedcontainers/>`_ (Grant Jenks) — a pure-Python
implementation whose *list-of-lists* design uses native list insertion and
binary search within short runs. Performance depends on the workload, collection
size and load factor. There is **no faithful stdlib substitute** (a
plain ``list`` + ``bisect.insort`` is ``O(n)`` per insert and easy to desync), so
this module is guarded: with the ``containers`` extra it exposes compatible
subclasses when the bundled native kernels are available and the original
upstream classes otherwise. Upstream classes are never modified. Without the
extra, each public name is a placeholder that
raises a clear ImportError on construction (see :func:`unstd.extras.missing`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING


# Same split as :mod:`unstd.containers.persistent` — checker sees the backend,
# runtime keeps the guard. `sortedcontainers` ships no stubs today, so the plain
# try/except also happens to check clean; writing it this way means the day it
# adds them is not the day this module starts failing the type gate.
if TYPE_CHECKING:
    from sortedcontainers import SortedDict, SortedList, SortedSet
    from sortedcontainers import SortedKeyList as SortedKeyList

    HAVE_SORTEDCONTAINERS = True
    _HAVE_NATIVE = False
else:
    try:
        import sortedcontainers as _backend
        from sortedcontainers import SortedDict, SortedList, SortedSet
        from sortedcontainers import SortedKeyList as SortedKeyList

        HAVE_SORTEDCONTAINERS = True
    except ImportError:  # base install without the `containers` extra
        from unstd.extras import missing

        HAVE_SORTEDCONTAINERS = False
        _HAVE_NATIVE = False
        SortedDict, SortedList, SortedSet = (
            missing(f"containers.{name}", "containers")
            for name in ("SortedDict", "SortedList", "SortedSet")
        )
    else:
        _HAVE_NATIVE = False
        # The native kernels use the verified 2.4.0 internal list/index layout.
        # A future dependency release remains usable through its own classes.
        if getattr(_backend, "__version__", None) == "2.4.0":
            try:
                from unstd.containers.ordering import (
                    SortedDict,
                    SortedList,
                    SortedSet,
                )
                from unstd.containers.ordering import (
                    SortedKeyList as SortedKeyList,
                )
            except ImportError:
                pass
            else:
                _HAVE_NATIVE = True


__all__ = ["HAVE_SORTEDCONTAINERS", "SortedDict", "SortedList", "SortedSet"]
