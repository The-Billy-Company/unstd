"""Persistent immutable mapping — the ``immutables`` HAMT :class:`Map`.

Where stdlib's ``types.MappingProxyType`` gives a *read-only view* of a mutable
dict (mutate the original and the "frozen" view changes underneath you), a
persistent :class:`Map` is genuinely immutable: :meth:`Map.set` / :meth:`Map.delete`
return a **new** map and leave the original untouched, sharing all unchanged
structure between the two versions rather than deep-copying. That makes "snapshot
this state and hand it to a concurrent reader" an ``O(log n)`` operation with no
copy and no lock — the property stdlib has no container for.

Backend: `immutables <https://github.com/MagicStack/immutables>`_ — a
C (with a pure-Python fallback) Hash Array Mapped Trie. The HAMT is Phil
Bagwell's structure ("Ideal Hash Trees", 2001), and this is the very
implementation CPython vendored to back :mod:`contextvars` (PEP 567), so its
persistence/structural-sharing semantics are battle-tested in the interpreter
itself. There is **no faithful stdlib substitute** (``MappingProxyType`` is a
view, not a value; ``dict(old, **{k: v})`` is a full ``O(n)`` copy with no
sharing), so this module is guarded: with the ``containers`` extra it re-exports
the real class verbatim (``unstd.containers.Map`` *is* ``immutables.Map``);
without it, :class:`Map` is a placeholder that raises a clear ImportError on
construction (see :func:`unstd._extra.missing`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING


# The type checker reads the real backend; the runtime keeps the guard. Splitting
# them is what lets `Map` stay a genuine generic class to a caller's annotations
# while still degrading to `_extra.missing` on a base install — rebinding a name
# that a checker knows to be a class is otherwise an error, and `immutables`
# (unlike `sortedcontainers`) ships `py.typed`, so it is one here.
if TYPE_CHECKING:
    from immutables import Map

    HAVE_IMMUTABLES = True
else:
    try:
        from immutables import Map

        HAVE_IMMUTABLES = True
    except ImportError:  # base install without the `containers` extra
        from unstd._extra import missing

        HAVE_IMMUTABLES = False
        Map = missing("containers.Map", "containers")


__all__ = ["HAVE_IMMUTABLES", "Map"]
