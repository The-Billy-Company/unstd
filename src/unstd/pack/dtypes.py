"""Portable numeric dtype grammar and bounded compiled-struct caches."""

from __future__ import annotations

from functools import lru_cache
import struct as _struct


__all__ = ()


# NumPy typestr kind+itemsize → stdlib ``struct` format char (standard sizes).
_STRUCT_CHAR = {
    "i1": "b",
    "u1": "B",
    "i2": "h",
    "u2": "H",
    "i4": "i",
    "u4": "I",
    "i8": "q",
    "u8": "Q",
    "f2": "e",
    "f4": "f",
    "f8": "d",
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


def _struct_spec(dtype: object) -> tuple[str, str, int]:
    """Lower a NumPy dtype string to ``(byteorder, struct_char, itemsize)``.

    Accepts a typestr (``"<f4"``, ``">i8"``, ``"u1"``) or a spelled-out name
    (``"float32"``). ``"="``/``"|"``/no prefix all mean native standard size — which
    maps to stdlib ``struct``'s ``"="`` (native order, standard size, no alignment),
    exactly the layout ``numpy.ndarray.tobytes`` produces for the same dtype.
    """
    if type(dtype) is str:
        return _cached_struct_spec(dtype)
    return _parse_struct_spec(dtype)


@lru_cache(maxsize=128)
def _cached_struct_spec(dtype: str) -> tuple[str, str, int]:
    """Reuse parsed formats for repeated calls without retaining arbitrary dtypes."""
    return _parse_struct_spec(dtype)


def _parse_struct_spec(dtype: object) -> tuple[str, str, int]:
    """Parse a dtype, preserving conversion and error behavior for foreign objects."""
    s = str(dtype)
    order = "="
    if s and s[0] in "<>=|":
        order = "=" if s[0] == "|" else s[0]
        s = s[1:]
    s = _ALIAS.get(s, s)
    # Exact typestr lookup: int() would also admit "i 4", "i0_4" or Unicode digits.
    try:
        char = _STRUCT_CHAR[s]
    except KeyError as exc:
        msg = f"unstd.pack: unsupported array dtype {dtype!r}"
        raise _struct.error(msg) from exc
    return order, char, int(s[1:])


@lru_cache(maxsize=128)
def _array_struct(order: str, char: str, count: int) -> _struct.Struct:
    """Reuse a compiled format whose bound pack method accepts only values."""
    return _struct.Struct(f"{order}{count}{char}")
