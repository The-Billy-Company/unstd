"""The deferred-raise placeholder every capability without a stdlib fallback binds.

Where a backend has no faithful substitute (a sorted container, a persistent
trie, a TOML writer), its public name still has to exist so the module imports
cleanly on a base install. :func:`missing` is that name: calling it raises an
``ImportError`` naming the extra that fixes it, so a consumer that never touches
the capability pays nothing, and one that does gets a one-line fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Never


if TYPE_CHECKING:
    from collections.abc import Callable


def missing(qualname: str, extra: str) -> Callable[..., Never]:
    """A stand-in for ``unstd.<qualname>`` that raises naming the ``extra`` it needs."""
    msg = f"unstd.{qualname} requires the {extra!r} extra — install with: pip install 'unstd[{extra}]'"

    def _raise(*_args: object, **_kwargs: object) -> Never:
        raise ImportError(msg)

    _raise.__name__ = _raise.__qualname__ = qualname.rpartition(".")[2]
    _raise.__doc__ = f"Unavailable — {msg}."
    return _raise
