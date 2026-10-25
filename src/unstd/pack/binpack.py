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

:func:`pack_array_portable` / :func:`unpack_array_portable` always retain struct's
portable dtype subset and conversion rules, returning bytes / a fresh list of
Python scalars regardless of whether NumPy is installed. Choose these helpers
when validation and return types must remain stable across installations.

Backend & fallback: NumPy is provided by the ``pack`` extra. The scalar
struct surface never needs it; the array helpers prefer it and degrade to
``struct`` when it is absent. The explicit portable helpers retain struct's
conversions, with a primitive buffer shortcut for matching integer and float64 arrays.
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
from operator import index as _index
import struct as _struct
from typing import TYPE_CHECKING

from unstd.pack.buffers import _pack_buffer
from unstd.pack.dtypes import _array_struct, _struct_spec


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
    Matching one-dimensional primitive arrays can snapshot their byte storage
    directly when doing so preserves struct's numeric conversions.
    """
    return _pack_array_struct(dtype, seq)


def _pack_array_struct(dtype: DTypeLike, seq: Iterable[float]) -> bytes:
    """Shared struct body, retaining legacy dtype-object handling."""
    order, char, size = _struct_spec(dtype)
    packed = _pack_buffer(order, char, size, seq)
    if packed is not None:
        return packed
    # Freeze before reading the count. The bound method reuses this tuple for
    # argument expansion, avoiding a second copy to prepend a format argument.
    vals = tuple(seq)
    return _array_struct(order, char, len(vals)).pack(*vals)


def unpack_array(
    dtype: DTypeLike,
    buf: bytes | bytearray | memoryview,
    *,
    count: int = -1,
    offset: int = 0,
) -> _ArrayResult:
    """Unpack ``dtype``-typed contiguous bytes into an array of scalars.

    With NumPy present this returns a **zero-copy** ``numpy.frombuffer`` view
    whose writability follows ``buf``; without it, a ``list`` decoded via stdlib
    ``struct``. With the default ``count=-1``, the remaining buffer's byte size
    must be a whole multiple of the dtype's itemsize (a partial trailing
    element raises, matching ``numpy.frombuffer``).

    ``offset`` selects a byte position in the original buffer; ``count``
    selects elements, or ``-1`` selects the whole remainder. Explicit counts
    permit unrelated trailing bytes. NumPy views retain the original buffer
    without slicing or copying it; fallback results are independent lists.
    Bounds, conversion, and errors follow the selected backend.
    """
    if _HAVE_NUMPY:
        return np.frombuffer(buf, dtype=dtype, count=count, offset=offset)
    return _unpack_array_struct(dtype, buf, count=count, offset=offset)


def unpack_array_portable(
    dtype: str,
    buf: bytes | bytearray | memoryview,
    *,
    count: int = -1,
    offset: int = 0,
) -> list[float]:
    """Decode portable numeric bytes to a fresh list, independent of NumPy.

    Uses the same dtype subset as :func:`pack_array_portable`. Buffers must be
    contiguous; default whole-remainder decoding rejects partial elements with
    :class:`error`. Contiguous typed and multidimensional memoryviews use their
    full byte size. The result contains Python scalars and does not alias *buf*.

    ``offset`` is a byte position, including unaligned positions. ``count``
    is the number of elements, or ``-1`` for the whole remaining buffer.
    Explicit counts allow trailing bytes beyond the selected elements.
    Both arguments accept ``__index__``; counts below ``-1`` raise
    :class:`ValueError`, and invalid byte ranges raise :class:`error`.
    """
    return _unpack_array_struct(dtype, buf, count=count, offset=offset)


def _unpack_array_struct(
    dtype: DTypeLike,
    buf: bytes | bytearray | memoryview,
    *,
    count: int = -1,
    offset: int = 0,
) -> list[float]:
    """Shared struct body, retaining legacy dtype-object and buffer handling."""
    order, char, size = _struct_spec(dtype)
    count = _index(count)
    offset = _index(offset)
    if count < -1:
        msg = "unstd.pack: count must be -1 or nonnegative"
        raise ValueError(msg)
    # Other exporters' lengths count elements or a first dimension, not bytes;
    # scalar views have no length at all. Keep the original buffer for struct's
    # contiguous-buffer checks without copying or changing its interpretation.
    if type(buf) is bytes or type(buf) is bytearray:
        nbytes = len(buf)
    else:
        with memoryview(buf) as view:
            nbytes = view.nbytes
    if not 0 <= offset <= nbytes:
        msg = f"unstd.pack: offset is outside buffer size {nbytes}"
        raise error(msg)
    available = nbytes - offset
    if count == -1:
        if available % size:
            msg = (
                f"unstd.pack: buffer size {available} is not a multiple "
                f"of itemsize {size}"
            )
            raise error(msg)
        count = available // size
    elif count > available // size:
        msg = (
            f"unstd.pack: selected elements of itemsize {size} do not fit "
            f"in {available} bytes"
        )
        raise error(msg)
    return list(_array_struct(order, char, count).unpack_from(buf, offset))
