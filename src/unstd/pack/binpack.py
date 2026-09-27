"""Binary packing — the stdlib ``struct`` surface plus bulk-array acceleration.

``struct`` is already a C codec, so there is no faster third-party backend for the
*scalar* pack/unpack path: this module re-exports the stdlib surface unchanged
(``pack`` / ``unpack`` / ``unpack_from`` / ``pack_into`` / ``calcsize`` /
``iter_unpack`` / the compiled-format ``Struct`` class / ``error``) so it is
byte-for-byte identical to ``struct`` and a call site migrates by swapping the
import::

    import struct  # →
    from unstd.pack import binpack  # binpack.pack(...) / binpack.Struct(...)

The value this module *adds* is the case ``struct`` handles clumsily — packing and
unpacking a large homogeneous numeric array, where the idiomatic ``struct`` spelling
(``struct.pack(f"<{len(xs)}f", *xs)`` / ``struct.iter_unpack``) is both awkward and
slow. :func:`pack_array` / :func:`unpack_array` take a NumPy dtype string
(``"<f4"``, ``">i8"``, ``"u1"``, …) and, when NumPy is present (the ``pack``
extra), ride ``numpy.ndarray.tobytes`` for the encode and a **zero-copy**
``numpy.frombuffer`` view for the decode. Without the extra (a base ``unstd``
install) they transparently fall back to stdlib ``struct`` — same dtype grammar,
byte-identical output, returning a ``list`` instead of an ``ndarray`` — so the
surface works everywhere and only the array throughput differs.

Backend & fallback: NumPy is provided by the ``pack`` extra. The scalar
struct surface never needs it; the array helpers prefer it and degrade to
``struct`` when it is absent. The two backends are pinned to produce identical
bytes (see ``tests/pack/test_pack.py``).

Prior art:

- stdlib ``struct`` — the format-string binary (de)serializer this surface is
  faithful to (Python Software Foundation).
- NumPy ``frombuffer`` / ``ndarray.tobytes`` (Harris et al., 2020) — the
  zero-copy buffer↔array bridge the accelerated array path uses.
- Structure-aware numeric codecs (lossless f32 byte-plane split, int8 scalar
  quantization, a self-describing frame header) — the direction a future
  ``pack`` array backend would go; NumPy is the backend for now.
"""

from __future__ import annotations

import struct as _struct
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy as np
    from numpy.typing import DTypeLike, NDArray

    _HAVE_NUMPY = True
else:
    try:
        import numpy as np

        _HAVE_NUMPY = True
    except (
        ImportError
    ):  # base install without the `pack` extra — stdlib struct fallback
        np = None
        _HAVE_NUMPY = False


# ── stdlib-``struct``-faithful surface (re-exported verbatim — byte-identical) ──
error = _struct.error
pack = _struct.pack
unpack = _struct.unpack
unpack_from = _struct.unpack_from
pack_into = _struct.pack_into
calcsize = _struct.calcsize
iter_unpack = _struct.iter_unpack
Struct = _struct.Struct

__all__ = [
    "Struct",
    "calcsize",
    "error",
    "iter_unpack",
    "pack",
    "pack_array",
    "pack_into",
    "unpack",
    "unpack_array",
    "unpack_from",
]

# NumPy typestr kind+itemsize → stdlib ``struct` format char (standard sizes).
_STRUCT_CHAR = {
    ("i", 1): "b",
    ("u", 1): "B",
    ("i", 2): "h",
    ("u", 2): "H",
    ("i", 4): "i",
    ("u", 4): "I",
    ("i", 8): "q",
    ("u", 8): "Q",
    ("f", 2): "e",
    ("f", 4): "f",
    ("f", 8): "d",
}
# Spelled-out dtype names → the ``kind+size`` typestr the parser understands.
_ALIAS = {
    "int8": "i1",
    "uint8": "u1",
    "int16": "i2",
    "uint16": "u2",
    "int32": "i4",
    "uint32": "u4",
    "int64": "i8",
    "uint64": "u8",
    "float16": "f2",
    "float32": "f4",
    "float64": "f8",
}


def _struct_spec(dtype: DTypeLike) -> tuple[str, str, int]:
    """Lower a NumPy dtype string to ``(byteorder, struct_char, itemsize)``.

    Accepts a typestr (``"<f4"``, ``">i8"``, ``"u1"``) or a spelled-out name
    (``"float32"``). ``"="``/``"|"``/no prefix all mean native standard size — which
    maps to stdlib ``struct``'s ``"="`` (native order, standard size, no alignment),
    exactly the layout ``numpy.ndarray.tobytes`` produces for the same dtype.
    """
    s = str(dtype)
    order = "="
    if s[:1] in "<>=|":
        order = "=" if s[0] == "|" else s[0]
        s = s[1:]
    s = _ALIAS.get(s, s)
    try:
        char = _STRUCT_CHAR[s[0], int(s[1:])]
    except (KeyError, ValueError) as exc:
        msg = f"unstd.pack: unsupported array dtype {dtype!r}"
        raise error(msg) from exc
    return order, char, int(s[1:])


def pack_array(dtype: DTypeLike, seq: Iterable[float]) -> bytes:
    """Pack a homogeneous numeric sequence into ``dtype``-typed contiguous bytes.

    ``dtype`` is a NumPy dtype (``"<f4"``, ``">i8"``, ``"u1"``, …). With NumPy
    present, a *sized* input (``ndarray``/``list``/``tuple``/anything with
    ``__len__``, the realistic call shape for a "bulk array" helper) goes straight
    through ``numpy.asarray`` — no intermediate Python list; a generic iterator/
    generator (no ``__len__``) streams through ``numpy.fromiter``, NumPy's own
    idiom for that case (``asarray`` cannot consume one directly). Without NumPy,
    the equivalent stdlib ``struct.pack`` — byte-identical output either way.
    """
    if _HAVE_NUMPY:
        dt = np.dtype(dtype)
        arr = (
            np.asarray(seq, dtype=dt)
            if hasattr(seq, "__len__")
            else np.fromiter(seq, dtype=dt)
        )
        return arr.tobytes()
    order, char, _ = _struct_spec(dtype)
    vals = list(seq)
    return _struct.pack(f"{order}{len(vals)}{char}", *vals)


def unpack_array(
    dtype: DTypeLike, buf: bytes | bytearray | memoryview
) -> NDArray[np.generic] | list[float]:
    """Unpack ``dtype``-typed contiguous bytes into an array of scalars.

    With NumPy present this returns a **zero-copy** read-only ``numpy.frombuffer``
    view over ``buf``; without it, a ``list`` decoded via stdlib ``struct``. Both
    require ``len(buf)`` to be a whole multiple of the dtype's itemsize (a partial
    trailing element raises, matching ``numpy.frombuffer``).
    """
    if _HAVE_NUMPY:
        return np.frombuffer(
            buf, dtype=dtype
        )  # accepts the spec as-is; no extra dtype build
    order, char, size = _struct_spec(dtype)
    if len(buf) % size:
        msg = f"unstd.pack: buffer size {len(buf)} is not a multiple of itemsize {size}"
        raise error(msg)
    return list(_struct.unpack(f"{order}{len(buf) // size}{char}", buf))
