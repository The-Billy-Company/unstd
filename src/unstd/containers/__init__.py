"""Container types the stdlib lacks — augmenting ``collections``.

``collections`` already ships the containers that are hard to beat: ``deque``,
``Counter``, ``defaultdict``, and ``OrderedDict`` are C-backed and stay where
they are. This group deliberately adds only what the stdlib has **no equivalent
for**:

- :class:`SortedDict` / :class:`SortedList` / :class:`SortedSet` — collections
  that keep themselves sorted through inserts and deletions (``sortedcontainers``).
- :class:`Map` — a persistent immutable HAMT mapping whose ``set``/``delete``
  return a new map sharing unchanged structure with the old (``immutables``).

Backend: both families ride the single ``containers`` extra
(``pip install 'unstd[containers]'`` → ``sortedcontainers`` + ``immutables``).
Neither has a faithful stdlib fallback, so — unlike ``serde.jsonx`` — there is no
slower-but-correct path: importing this package is always safe (the guarded
imports merely bind placeholders when a backend is absent), but *constructing* a
backed type without its backend raises a clear, actionable ImportError naming the
extra. Import the members directly::

    from unstd.containers import SortedDict, SortedList, SortedSet, Map

Prior art: ``sortedcontainers`` (Grant Jenks — pure-Python sorted collections,
list-of-lists design); ``immutables`` (a HAMT — Bagwell, "Ideal Hash Trees",
2001 — the same implementation backing CPython's ``contextvars``).
"""

from __future__ import annotations

from unstd.containers.persistent import Map
from unstd.containers.sorted import SortedDict, SortedList, SortedSet


__all__ = ["Map", "SortedDict", "SortedList", "SortedSet"]
