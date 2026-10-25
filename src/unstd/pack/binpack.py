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
install) they fall back to stdlib ``struct`` over a portable numeric dtype
subset, returning a ``list`` instead of an ``ndarray``. Compatible values have
the same layout; accepted dtypes, input coercion, and errors follow the backend.
For example, NumPy truncates floats for integer dtypes while struct rejects them.

:func:`pack_array_portable` / :func:`unpack_array_portable` always use struct's
portable dtype subset and conversion rules, returning bytes / a fresh list of
Python scalars regardless of whether NumPy is installed. Choose these helpers
when validation and return types must remain stable across installations.

Backend & fallback: NumPy is provided by the ``pack`` extra. The scalar
struct surface never needs it; the array helpers prefer it and degrade to
``struct`` when it is absent. The explicit portable helpers always use struct.
Tests pin shared layouts for compatible inputs and the portable validation rules.

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

from collections.abc import Iterable  # noqa: TC003 — runtime type introspection
import struct as _struct
from typing import TYPE_CHECKING

from unstd.pack._internal.dtype import _array_struct, _struct_spec


if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import DTypeLike, NDArray

    _HAVE_NUMPY = True
    _ArrayResult = NDArray[np.generic] | list[float]
else:
    try:
        import numpy as np

        _HAVE_NUMPY = True
    except (
        ImportError
    ):  # base install without the `pack` extra — stdlib struct fallback
        np = None
        _HAVE_NUMPY = False

    # Runtime consumers must be able to inspect signatures without importing
    # optional NumPy typing. The legacy dtype accepts backend-specific objects;
    # the result alias describes the shapes available on this installation.
    DTypeLike = object
    _ArrayResult = np.ndarray | list[float] if _HAVE_NUMPY else list[float]


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
    "pack_array_portable",
    "pack_into",
    "unpack",
    "unpack_array",
    "unpack_array_portable",
    "unpack_from",
]


def pack_array(dtype: DTypeLike, seq: Iterable[float]) -> bytes:
    """Pack a homogeneous numeric sequence into ``dtype``-typed contiguous bytes.

    ``dtype`` is a NumPy dtype (``"<f4"``, ``">i8"``, ``"u1"``, …). With NumPy
    present, a *sized* input (``ndarray``/``list``/``tuple``/anything with
    ``__len__``, the realistic call shape for a "bulk array" helper) goes straight
    through ``numpy.asarray`` — no intermediate Python list; a generic iterator/
    generator (no ``__len__``) streams through ``numpy.fromiter``, NumPy's own
    idiom for that case (``asarray`` cannot consume one directly). Without NumPy,
    :func:`pack_array_portable` applies struct's stricter numeric conversion and
    portable dtype subset. Compatible values have the same binary layout, but
    coercion and supported dtypes can differ. Use :func:`pack_array_portable`
    when validation must be independent of installed dependencies.
    """
    if _HAVE_NUMPY:
        arr = (
            np.asarray(seq, dtype=dtype)
            if hasattr(seq, "__len__")
            else np.fromiter(seq, dtype=dtype)
        )
        return arr.tobytes()
    return _pack_array_struct(dtype, seq)


def pack_array_portable(dtype: str, seq: Iterable[float]) -> bytes:
    """Pack numeric values with struct's validation, independent of NumPy.

    Accepts portable dtype strings: signed/unsigned integers of 1/2/4/8 bytes
    and floats of 2/4/8 bytes, with the byte-order prefixes and spelled-out
    aliases described in the module README. Integer formats require integer
    values (or ``__index__``); floats are not silently truncated. Values outside
    the format's range raise the same exception as stdlib ``struct.pack``.
    The input iterable is consumed once before values are converted.
    """
    return _pack_array_struct(dtype, seq)


def _pack_array_struct(dtype: DTypeLike, seq: Iterable[float]) -> bytes:
    """Shared struct body, retaining legacy dtype-object handling."""
    order, char, _ = _struct_spec(dtype)
    # Freeze before reading the count. The bound method reuses this tuple for
    # argument expansion, avoiding a second copy to prepend a format argument.
    vals = tuple(seq)
    return _array_struct(order, char, len(vals)).pack(*vals)


def unpack_array(dtype: DTypeLike, buf: bytes | bytearray | memoryview) -> _ArrayResult:
    """Unpack ``dtype``-typed contiguous bytes into an array of scalars.

    With NumPy present this returns a **zero-copy** ``numpy.frombuffer`` view
    whose writability follows ``buf``; without it, a ``list`` decoded via stdlib
    ``struct``. Both require the buffer's byte size to be a whole multiple of
    the dtype's itemsize (a partial trailing element raises, matching
    ``numpy.frombuffer``).
    """
    if _HAVE_NUMPY:
        return np.frombuffer(
            buf, dtype=dtype
        )  # accepts the spec as-is; no extra dtype build
    return _unpack_array_struct(dtype, buf)


def unpack_array_portable(
    dtype: str, buf: bytes | bytearray | memoryview
) -> list[float]:
    """Decode portable numeric bytes to a fresh list, independent of NumPy.

    Uses the same dtype subset as :func:`pack_array_portable`. Buffers must be
    contiguous and contain a whole number of elements; partial elements raise
    :class:`error`. Contiguous typed and multidimensional memoryviews use their
    full byte size. The result contains Python scalars and does not alias *buf*.
    """
    return _unpack_array_struct(dtype, buf)


def _unpack_array_struct(
    dtype: DTypeLike, buf: bytes | bytearray | memoryview
) -> list[float]:
    """Shared struct body, retaining legacy dtype-object and buffer handling."""
    order, char, size = _struct_spec(dtype)
    # Other exporters' lengths count elements or a first dimension, not bytes;
    # scalar views have no length at all. Keep the original buffer for struct's
    # contiguous-buffer checks without copying or changing its interpretation.
    if type(buf) is bytes or type(buf) is bytearray:
        nbytes = len(buf)
    else:
        with memoryview(buf) as view:
            nbytes = view.nbytes
    if nbytes % size:
        msg = f"unstd.pack: buffer size {nbytes} is not a multiple of itemsize {size}"
        raise error(msg)
    return list(_struct.unpack(f"{order}{nbytes // size}{char}", buf))
