"""Shared guard for the optional ``containers`` backends.

Both container families this group adds — the ``sortedcontainers`` sorted
collections and the ``immutables`` HAMT :class:`Map` — augment ``collections``
with structures the stdlib has **no equivalent for**, and both ride the single
``containers`` extra. Neither has a faithful stdlib fallback (a substitute would
be an ``O(n)`` list masquerading as an ``O(log n)`` sorted structure, or a
full-copy dict masquerading as a persistent trie — a silent, load-bearing lie),
so this module centralizes the one error contract they share: when a backend is
absent, bind a placeholder to its public name so the package still *imports*
cleanly, and raise a clear, actionable ImportError naming the extra only when a
backed type is actually *constructed*.

This is the deferred-raise variant of the ``serde.structs`` posture — same
message shape (``pip install 'unstd[containers]'``), but timed to first use so a
consumer that never touches these types pays nothing to import the package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Never


if TYPE_CHECKING:
    from collections.abc import Callable


_HINT = "install with: pip install 'unstd[containers]'"

__all__ = ["missing"]


def missing(name: str) -> Callable[..., Never]:
    """Return a constructor placeholder for a backend-backed type named ``name``.

    Bound to the public name (``SortedDict``, ``Map``, …) when its backend is not
    installed: importing ``unstd.containers`` stays crash-free, and only calling
    the placeholder raises an ImportError pointing at the ``containers`` extra.
    """

    def _raise(*_args: object, **_kwargs: object) -> Never:
        msg = f"unstd.containers.{name} requires the 'containers' extra — {_HINT}"
        raise ImportError(msg)

    _raise.__name__ = name
    _raise.__qualname__ = name
    _raise.__doc__ = f"Unavailable {name} — the 'containers' extra is not installed."
    return _raise
