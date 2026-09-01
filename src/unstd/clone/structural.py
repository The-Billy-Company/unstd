"""Fast structural deep clone — a msgspec round-trip stand-in for stdlib ``copy``.

``copy.deepcopy`` is a well-known performance sink: it is pure-Python, walks an
arbitrary object graph, and maintains a ``memo`` table to preserve identity and
handle cycles. For *wire-shaped data* — the ``dict``/``list``/``tuple``/``set``/
``frozenset``/``dataclass``/``msgspec.Struct`` payloads that cross a program's seams —
none of that generality is needed: the value is a finite tree of plain data, and
the fastest faithful clone is a **structural round-trip** through a native codec.

:func:`deep` takes that fast path when it can and falls back to ``copy.deepcopy``
otherwise, so the surface is safe on *any* object.

How the fast path stays *faithful* (two guarantees, not one):

- **Independence is structural.** The round-trip is encode-to-bytes then
  decode-back-to-``type(obj)`` (``msgspec.json``). A value rebuilt from bytes
  cannot share a single reference with the original, so the clone is deep and
  fully independent by construction — mutating it never touches the source.
  (This is why we do *not* use ``msgspec.convert(obj, type(obj))``: convert
  reuses already-correct nested objects, so its "clone" shares inner containers —
  a shallow copy in disguise.)
- **Value fidelity is verified.** Decoding to the concrete type restores the
  outer type (a ``Struct`` stays a ``Struct``, a ``tuple`` stays a ``tuple``, a
  ``set`` stays a ``set``), and for a fully-typed schema the whole value too. But
  an ``Any``/``object``-typed slot cannot carry a non-JSON-native Python type
  through JSON (a ``tuple`` in an ``Any`` field decodes back as a ``list``; a
  non-``str`` dict key comes back stringified). So :func:`deep` **compares the
  round-tripped clone to the original** and, on any mismatch (or any encode/decode
  error — a non-finite float, an int past 64-bit, an unencodable value), falls
  back to ``copy.deepcopy``. The fast path therefore only ever *returns* a
  value-equal, independent clone; everything else degrades to a correct deepcopy.

:func:`deep` is for **data**, not arbitrary live objects. Anything not plain
structural data — objects with a custom ``__deepcopy__``, file handles, locks,
cyclic graphs — skips the fast path and goes straight through ``copy.deepcopy``.

Backend & fallback: msgspec is provided by the ``clone`` extra
(``msgspec==0.21.1``, the same pin the ``serde`` extra carries). When it is
absent — a base ``unstd`` install, which is what this dev env runs — *every* call
takes the ``copy.deepcopy`` fallback: same result, just without the structural
speedup. Consumers that opt into ``unstd[clone]`` get the fast path automatically.

The faithful ``copy``/``deepcopy`` re-exports let a file migrating off ``import
copy`` swap the import (``from unstd import clone``) and keep ``clone.copy`` /
``clone.deepcopy`` working unchanged, reaching for ``clone.deep`` only where the
structural fast path is wanted.

Prior art:

- **msgspec** (Jim Crist-Harif — https://github.com/jcrist/msgspec): a
  high-performance serialization + validation library; the ``msgspec.json``
  encode/decode pair performs the structural round-trip this module rides.
- stdlib **``copy``** (``copy.copy`` / ``copy.deepcopy``): the general-purpose
  object-graph copier this accelerates for the structural-data case and falls
  back to for everything else.
"""

from __future__ import annotations

import copy as _stdlib
import dataclasses
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import msgspec

    _HAVE_MSGSPEC = True
else:
    try:
        import msgspec

        _HAVE_MSGSPEC = True
    except (
        ImportError
    ):  # base install without the `clone` extra — stdlib deepcopy fallback
        msgspec = None
        _HAVE_MSGSPEC = False


copy = _stdlib.copy
deepcopy = _stdlib.deepcopy

__all__ = ["copy", "deep", "deepcopy"]

# Builtin container types the msgspec round-trip can rebuild as themselves.
_FAST: tuple[type, ...] = (dict, list, tuple, set, frozenset)


def _structural(obj: object) -> bool:
    """Return whether ``obj`` is plain structural data a msgspec round-trip can rebuild."""
    return (
        isinstance(obj, _FAST)
        or (_HAVE_MSGSPEC and isinstance(obj, msgspec.Struct))
        or (dataclasses.is_dataclass(obj) and not isinstance(obj, type))
    )


def deep[T](obj: T) -> T:
    """Deep-clone ``obj`` — fast msgspec structural round-trip, else ``copy.deepcopy``.

    For structural data (``dict``/``list``/``tuple``/``set``/``frozenset``/
    ``dataclass``/``msgspec.Struct``) with the ``clone`` extra installed, this
    round-trips through ``msgspec.json`` for a speedup over ``copy.deepcopy`` while
    producing a fully independent, type-preserving clone — verified value-equal to
    the original before it is returned. Without the extra — or for any value the
    round-trip cannot faithfully reproduce, or any non-structural object — it falls
    back to ``copy.deepcopy``. Use it for *data*; objects with a custom
    ``__deepcopy__``, open handles, or locks always take the ``deepcopy`` path.
    """
    if _HAVE_MSGSPEC and _structural(obj):
        try:
            twin = msgspec.json.decode(msgspec.json.encode(obj), type=type(obj))
            if (
                twin == obj
            ):  # value fidelity (independence is inherent to the round-trip)
                return twin
        except (
            msgspec.EncodeError,
            msgspec.DecodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            pass  # unencodable / lossy / cyclic — fall through to a faithful deepcopy
    return _stdlib.deepcopy(obj)
