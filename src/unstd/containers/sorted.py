"""Always-sorted collections — ``sortedcontainers`` under a thin re-export.

The stdlib gives you ``bisect`` over a hand-maintained list, but no container
that *keeps itself* sorted through inserts and deletions. This module exposes the
three that fill that gap:

- :class:`SortedDict` — a ``dict`` whose keys iterate in sorted order.
- :class:`SortedList` — a mutable sequence kept sorted; ``O(log n)`` membership,
  rank, and range queries via ``bisect``/``irange``.
- :class:`SortedSet` — a ``set`` with sorted iteration and indexable ordering.

Backend: these come from `sortedcontainers
<https://grantjenks.com/docs/sortedcontainers/>`_ (Grant Jenks) — a pure-Python
implementation whose *list-of-lists* design keeps each inner run short enough
that ``memmove``/``bisect`` on native C lists beats a tree's pointer-chasing in
practice, so it is competitive with, and often faster than, C-extension balanced
trees while staying pure Python. There is **no faithful stdlib substitute** (a
plain ``list`` + ``bisect.insort`` is ``O(n)`` per insert and easy to desync), so
this module is guarded: with the ``containers`` extra it re-exports the real
classes verbatim (a thin, faithful wrapper — ``unstd.containers.SortedDict`` *is*
``sortedcontainers.SortedDict``); without it, each name is a placeholder that
raises a clear ImportError on construction (see :mod:`unstd.containers.guard`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING


# Same split as :mod:`unstd.containers.persistent` — checker sees the backend,
# runtime keeps the guard. `sortedcontainers` ships no stubs today, so the plain
# try/except also happens to check clean; writing it this way means the day it
# adds them is not the day this module starts failing the type gate.
if TYPE_CHECKING:
    from sortedcontainers import SortedDict, SortedList, SortedSet

    HAVE_SORTEDCONTAINERS = True
else:
    try:
        from sortedcontainers import SortedDict, SortedList, SortedSet

        HAVE_SORTEDCONTAINERS = True
    except ImportError:  # base install without the `containers` extra
        from unstd.containers.guard import missing

        HAVE_SORTEDCONTAINERS = False
        SortedDict = missing("SortedDict")
        SortedList = missing("SortedList")
        SortedSet = missing("SortedSet")


__all__ = ["HAVE_SORTEDCONTAINERS", "SortedDict", "SortedList", "SortedSet"]
