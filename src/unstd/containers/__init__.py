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
extra. Each backend loads only when one of its types is requested, so a ``Map``
consumer does not import the sorted collections. Import the members directly::

    from unstd.containers import SortedDict, SortedList, SortedSet, Map

Prior art: ``sortedcontainers`` (Grant Jenks — pure-Python sorted collections,
list-of-lists design); ``immutables`` (a HAMT — Bagwell, "Ideal Hash Trees",
2001 — the same implementation backing CPython's ``contextvars``).
"""

from __future__ import annotations

from builtins import sorted as _sorted
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from unstd.containers import persistent as persistent
    from unstd.containers import sorted as sorted
    from unstd.containers.persistent import Map
    from unstd.containers.sorted import SortedDict, SortedList, SortedSet


__all__ = ["Map", "SortedDict", "SortedList", "SortedSet"]
_SUBMODULES = ("persistent", "sorted")

# A reload restores the same lazy surface even after earlier member access.
for _name in __all__:
    globals().pop(_name, None)
del _name


def __getattr__(name: str) -> Any:
    """Resolve and cache a public type through its guarded backend module."""
    resolved: Any
    if name in _SUBMODULES:
        from importlib import import_module

        resolved = import_module(f"{__name__}.{name}")
    elif name == "Map":
        from unstd.containers.persistent import Map

        resolved = Map
    elif name in {"SortedDict", "SortedList", "SortedSet"}:
        from unstd.containers import sorted as sorted_

        resolved = getattr(sorted_, name)
    else:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    globals()[name] = resolved
    return resolved


def __dir__() -> list[str]:
    """Include every public type without loading either backend."""
    return _sorted({*globals(), *__all__, *_SUBMODULES})
