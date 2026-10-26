"""Primitive buffer copies that retain portable struct conversion semantics."""

from __future__ import annotations

from array import array
import sys
from typing import TYPE_CHECKING, cast


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Buffer


_NATIVE_ORDER = "<" if sys.byteorder == "little" else ">"
_SIGNED = "bhilq"
_UNSIGNED = "BHILQ"


def _pack_buffer(order: str, char: str, size: int, seq: object) -> bytes | None:
    """Snapshot exact primitive arrays when per-value struct coercion is inert.

    Integer buffers must retain signedness and width. Only double precision
    floats can be copied: packing float16/float32 through Python floats can
    quiet signaling NaNs. Other iterables, array subclasses and dimensions keep
    the normal iterable snapshot and numeric conversion path.
    """
    source_type = type(seq)
    if source_type is list or source_type is tuple:
        return None
    if source_type is not array:
        # A caller holding an ndarray has already imported NumPy. Looking up
        # its exact class keeps portable packing independent of the extra and
        # preserves subclasses' iteration and numeric hooks.
        numpy = sys.modules.get("numpy")
        if numpy is None or source_type is not getattr(numpy, "ndarray", None):
            return None

    try:
        view = memoryview(cast("Buffer", seq))
    except (BufferError, TypeError, ValueError):
        # Some NumPy dtypes (including datetime64) are iterable but do not
        # export a PEP 3118 buffer. Their scalars still use struct conversion.
        return None
    with view:
        if view.ndim != 1 or view.itemsize != size:
            return None
        source_char = view.format
        source_order = _NATIVE_ORDER
        if source_char[:1] in ("<", ">", "!", "=", "@"):
            prefix, source_char = source_char[0], source_char[1:]
            if prefix in ("<", ">"):
                source_order = prefix
            elif prefix == "!":
                source_order = ">"
        if char in _SIGNED:
            if source_char not in _SIGNED:
                return None
        elif char in _UNSIGNED:
            if source_char not in _UNSIGNED:
                return None
        elif char != "d" or source_char != "d" or size != 8:
            return None

        target_order = _NATIVE_ORDER if order == "=" else order
        if size == 1 or source_order == target_order:
            # tobytes walks a one-dimensional strided ndarray in iteration
            # order, and the exact primitive source has no conversion hooks.
            return view.tobytes()

        swapped = array(char)
        if swapped.itemsize != size:
            return None
        swapped.frombytes(view.tobytes())
        swapped.byteswap()
        return swapped.tobytes()
