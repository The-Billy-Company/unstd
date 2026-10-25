"""Validated immutable numeric rows preceded by an integer boundary table.

The wire layout is an integer offset table followed by a contiguous numeric
matrix. Offsets count matrix rows, start at zero, and end at the matrix's row
count. Application identity, publication budgets, and normalization stay with
the caller. A compiled decoder reuses its dtype and geometry configuration;
each unpack still validates the complete input without retaining prior results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
import math
from operator import index
import struct
import sys
from types import ModuleType  # noqa: TC003 — runtime annotation introspection
from typing import TYPE_CHECKING, cast

from unstd.pack import binpack
from unstd.pack.dtypes import _array_struct, _struct_spec


if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    from numpy.typing import NDArray

    _Values = NDArray[np.generic] | tuple[tuple[float, ...], ...]
    _Indices = NDArray[np.generic] | tuple[int, ...]
else:
    _Values = (
        binpack.np.ndarray | tuple[tuple[float, ...], ...]
        if binpack._HAVE_NUMPY
        else tuple[tuple[float, ...], ...]
    )
    _Indices = (
        binpack.np.ndarray | tuple[int, ...] if binpack._HAVE_NUMPY else tuple[int, ...]
    )


_native: ModuleType | None
try:
    _native = import_module("unstd.pack._pack_native")
except ImportError:
    _native = None


__all__ = ["RaggedDecoder"]


def _snapshot(buf: bytes | bytearray | memoryview) -> bytes:
    """Retain exact immutable bytes; snapshot every other contiguous exporter."""
    if type(buf) is bytes:
        return buf
    with memoryview(buf) as view:
        if not view.c_contiguous:
            msg = "ragged input must be C-contiguous"
            raise BufferError(msg)
        return view.tobytes()


def _dtype(spec: tuple[str, str, int]) -> str:
    """Canonical NumPy spelling from the already validated portable grammar."""
    order, char, size = spec
    kind = "f" if char in "efd" else "u" if char.isupper() else "i"
    return f"{order}{kind}{size}"


@dataclass(frozen=True, slots=True)
class RaggedDecoder:
    """A portable numeric frame layout with immutable decoded storage.

    ``columns`` is the matrix row width; ``index_count`` is supplied to
    :meth:`unpack` because the surrounding protocol owns the table length.
    Integer boundaries must start at zero and end at the derived row count.
    ``allow_empty=False`` requires strict increases; otherwise equal adjacent
    offsets represent empty segments. ``finite=True`` rejects NaNs and
    infinities in floating-point values, scanning every frame independently.

    NumPy results are read-only views over immutable bytes. Other exporters,
    including read-only views of mutable storage, are copied before validation.
    Without NumPy, decoding returns immutable tuples of rows and boundaries.
    """

    dtype: str
    columns: int = field(kw_only=True)
    index_dtype: str = field(default="=i8", kw_only=True)
    finite: bool = field(default=False, kw_only=True)
    allow_empty: bool = field(default=True, kw_only=True)
    _value_spec: tuple[str, str, int] = field(init=False, repr=False)
    _index_spec: tuple[str, str, int] = field(init=False, repr=False)
    _value_dtype: str = field(init=False, repr=False)
    _index_dtype: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Normalize portable dtype and bounded row-width configuration once."""
        if not isinstance(self.dtype, str) or not isinstance(self.index_dtype, str):
            msg = "ragged dtypes must be strings"
            raise TypeError(msg)
        value_spec = _struct_spec(self.dtype)
        index_spec = _struct_spec(self.index_dtype)
        if index_spec[1] not in "bBhHiIqQ":
            msg = "ragged boundaries require an integer dtype"
            raise ValueError(msg)
        columns = index(self.columns)
        if columns <= 0 or columns > sys.maxsize // value_spec[2]:
            msg = "ragged columns exceed the supported row width"
            raise ValueError(msg)
        if type(self.finite) is not bool or type(self.allow_empty) is not bool:
            msg = "ragged finite and allow_empty flags must be bool"
            raise TypeError(msg)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "_value_spec", value_spec)
        object.__setattr__(self, "_index_spec", index_spec)
        object.__setattr__(self, "_value_dtype", _dtype(value_spec))
        object.__setattr__(self, "_index_dtype", _dtype(index_spec))

    def unpack(
        self, buf: bytes | bytearray | memoryview, *, index_count: int
    ) -> tuple[_Values, _Indices]:
        """Validate and decode a complete boundary-table/matrix frame.

        The table contains at least one integer. Every byte after the table
        belongs to complete matrix rows. Invalid geometry, boundaries, or
        configured finite-value constraints raise :class:`ValueError`.
        Both ``columns`` and ``index_count`` accept ``__index__`` values.
        """
        count = index(index_count)
        if count < 1:
            msg = "ragged frames require at least one boundary"
            raise ValueError(msg)
        data = _snapshot(buf)
        if count > len(data) // self._index_spec[2]:
            msg = "ragged boundary table exceeds the input buffer"
            raise ValueError(msg)
        if _native is None:
            rows = self._validate(data, count)
        else:
            validate = cast(
                "Callable[[bytes, int, int, str, str, str, str, bool, bool], int]",
                _native.validate,
            )
            rows = validate(
                data,
                count,
                self.columns,
                self._value_spec[1],
                self._value_spec[0],
                self._index_spec[1],
                self._index_spec[0],
                self.finite,
                self.allow_empty,
            )
        table_bytes = count * self._index_spec[2]
        if binpack._HAVE_NUMPY:
            backend = vars(binpack)["np"]
            values = backend.frombuffer(
                data, dtype=self._value_dtype, offset=table_bytes
            ).reshape(rows, self.columns)
            boundaries = backend.frombuffer(data, dtype=self._index_dtype, count=count)
            return values, boundaries
        order, char, _ = self._value_spec
        values_tuple = tuple(
            _array_struct(order, char, self.columns).iter_unpack(
                memoryview(data)[table_bytes:]
            )
        )
        order, char, _ = self._index_spec
        indices = _array_struct(order, char, count).unpack_from(data)
        return values_tuple, indices

    def _validate(self, data: bytes, count: int) -> int:
        """The stdlib implementation of the native validator's complete contract."""
        table_bytes = count * self._index_spec[2]
        available = len(data) - table_bytes
        width = self.columns * self._value_spec[2]
        if available % width:
            msg = "ragged value bytes do not contain complete rows"
            raise ValueError(msg)
        rows = available // width
        order, char, _ = self._index_spec
        previous = 0
        for position, (boundary,) in enumerate(
            struct.iter_unpack(f"{order}{char}", memoryview(data)[:table_bytes])
        ):
            if (
                boundary < 0
                or boundary > rows
                or (position == 0 and boundary != 0)
                or (position and boundary < previous)
                or (position and not self.allow_empty and boundary == previous)
            ):
                msg = "invalid ragged row boundaries"
                raise ValueError(msg)
            previous = boundary
        if previous != rows:
            msg = "ragged boundaries do not end at the row count"
            raise ValueError(msg)
        order, char, _ = self._value_spec
        if self.finite and char in "efd":
            for (value,) in struct.iter_unpack(
                f"{order}{char}", memoryview(data)[table_bytes:]
            ):
                if not math.isfinite(value):
                    msg = "ragged values must be finite"
                    raise ValueError(msg)
        return rows
