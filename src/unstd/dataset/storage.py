"""Indexed JSON file geometry, bounded construction, and mapped byte access.

The private format is versioned independently of the Python API. A fixed header
precedes newline-terminated JSON records and a little-endian uint64 offset table.
No table or decoded corpus is retained in Python memory.
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003 — public annotation introspection
from hashlib import sha256
import mmap
from os import PathLike  # noqa: TC003 — public annotation introspection
from pathlib import Path
import struct
from tempfile import TemporaryFile
from typing import TYPE_CHECKING, Protocol

from unstd import fs
from unstd.serde import jsonx
from unstd.serde.jsonx import Json  # noqa: TC001 — public annotation introspection


if TYPE_CHECKING:
    from collections.abc import Buffer

    from unstd.dataset._dataset_native import Mapping

    type _Map = mmap.mmap | Mapping


class _Kernel(Protocol):
    """The narrow typed boundary shared by the native and portable readers."""

    def map(self, descriptor: int, /) -> Mapping | None:
        """A private read-only mapping where native OS support is available."""

    def row(
        self, data: Buffer, index_offset: int, count: int, position: int, /
    ) -> bytes:
        """One checked row, excluding its framing newline."""

    def take(
        self, data: Buffer, index_offset: int, count: int, positions: tuple[int, ...], /
    ) -> list[bytes]:
        """Checked rows in request order."""

    def span(
        self, data: Buffer, index_offset: int, count: int, start: int, stop: int, /
    ) -> bytes:
        """One checked contiguous span, including framing newlines."""

    def validate(self, data: Buffer, index_offset: int, count: int, /) -> None:
        """Every row boundary and framing newline is valid."""


_native: _Kernel | None
try:
    from unstd.dataset import _dataset_native as _implementation
except ImportError:
    _native = None
else:
    _native = _implementation if _implementation.KERNEL_VERSION == 2 else None


__all__ = []

_MAGIC = b"UNSTDDS\x02"
_HEADER = struct.Struct("<8sQQ32s")
_OFFSET = struct.Struct("<Q")
_PAIR = struct.Struct("<QQ")
_CHUNK = 1 << 20


def _invalid() -> ValueError:
    """A stable format error independent of the optional native implementation."""
    return ValueError("invalid dataset format or row offsets")


def build(
    path: str | PathLike[str],
    rows: Iterable[Json],
    *,
    max_row_bytes: int = 16_777_216,
) -> int:
    """Atomically publish an indexed JSON snapshot and return its row count.

    Input is consumed once. JSON encoding rejects nonfinite floats and follows
    stdlib normalization for tuples, object keys, and lone surrogates. Each row
    must fit the positive integer *max_row_bytes*, excluding its framing newline.
    Encoding allocates the complete row before checking that limit.

    Memory is proportional to one row and fixed I/O buffers. Offsets spool to a
    temporary file. Failures before replacement preserve the existing artifact;
    cancellation reported after replacement can leave the new artifact committed.
    Publication uses ``fs.atomic_writer`` including file synchronization, without
    promising parent-directory persistence across power loss.
    """
    if type(max_row_bytes) is not int or max_row_bytes <= 0:
        msg = "max_row_bytes must be a positive integer"
        raise ValueError(msg)
    destination = Path(path).absolute()
    fs.ensure_dir(destination.parent)
    count = 0
    digest = sha256()
    with (
        TemporaryFile(mode="w+b", dir=destination.parent) as index,
        fs.atomic_writer(destination, mode="wb") as target,
    ):
        target.write(bytes(_HEADER.size))
        for row in rows:
            payload = jsonx.dumps(
                row, ensure_ascii=True, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            if len(payload) > max_row_bytes:
                msg = f"row {count} exceeds max_row_bytes={max_row_bytes}"
                raise ValueError(msg)
            index.write(_OFFSET.pack(target.tell()))
            target.write(payload)
            target.write(b"\n")
            digest.update(payload)
            digest.update(b"\n")
            count += 1
        index_offset = target.tell()
        index.write(_OFFSET.pack(index_offset))
        index.seek(0)
        while chunk := index.read(_CHUNK):
            target.write(chunk)
            digest.update(chunk)
        target.seek(0)
        target.write(_HEADER.pack(_MAGIC, count, index_offset, digest.digest()))
    return count


def _header(raw: bytes) -> tuple[int, int, bytes]:
    """The admitted format header, before mapping or touching the offset table."""
    if len(raw) != _HEADER.size:
        raise _invalid()
    magic, count, index_offset, digest = _HEADER.unpack(raw)
    if magic != _MAGIC:
        raise _invalid()
    return count, index_offset, digest


def _geometry(data: _Map) -> tuple[int, int, bytes]:
    """A constant-time header and endpoint proof before indexed operations."""
    if len(data) < _HEADER.size + _OFFSET.size:
        raise _invalid()
    count, index_offset, digest = _header(data[: _HEADER.size])
    if (
        index_offset < _HEADER.size
        or index_offset > len(data)
        or count > (len(data) - index_offset) // _OFFSET.size - 1
        or len(data) != index_offset + (count + 1) * _OFFSET.size
        or _OFFSET.unpack_from(data, index_offset)[0] != _HEADER.size
        or _OFFSET.unpack_from(data, len(data) - _OFFSET.size)[0] != index_offset
        or (count == 0 and index_offset != _HEADER.size)
        or (count > 0 and index_offset == _HEADER.size)
    ):
        raise _invalid()
    return count, index_offset, digest


class Store:
    """An owned read-only mapping with checked row extraction and integrity checks."""

    def __init__(self, path: str | PathLike[str]) -> None:
        """Validate file geometry before admitting any indexed read."""
        self._data: _Map | None = None
        data: _Map | None = None
        try:
            with Path(path).open("rb") as stream:
                _header(stream.read(_HEADER.size))
                data = None if _native is None else _native.map(stream.fileno())
                if data is None:
                    data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
            self.count, self.index_offset, self._digest = _geometry(data)
        except BaseException:
            if data is not None:
                data.close()
            raise
        self._data = data

    def opened(self) -> _Map:
        """The mapping, rejecting every read after its owner closes it."""
        if self._data is None:
            msg = "dataset is closed"
            raise ValueError(msg)
        return self._data

    def bounds(self, data: _Map, position: int) -> tuple[int, int]:
        """One checked absolute row range, including the framing newline."""
        start, stop = _PAIR.unpack_from(
            data, self.index_offset + position * _OFFSET.size
        )
        if not _HEADER.size <= start < stop - 1 < self.index_offset:
            raise _invalid()
        if data[stop - 1] != 10 or data.find(b"\n", start, stop - 1) != -1:
            raise _invalid()
        return start, stop

    def row(self, position: int) -> bytes:
        """One encoded JSON value, with its framing newline removed."""
        data = self.opened()
        if _native is not None:
            return _native.row(data, self.index_offset, self.count, position)
        start, stop = self.bounds(data, position)
        return data[start : stop - 1]

    def take(self, positions: tuple[int, ...]) -> list[bytes]:
        """Encoded rows in requested order, using one native call when available."""
        data = self.opened()
        if _native is not None:
            return _native.take(data, self.index_offset, self.count, positions)
        return [
            data[start : stop - 1]
            for start, stop in (self.bounds(data, position) for position in positions)
        ]

    def span(self, start: int, stop: int) -> bytes:
        """A contiguous checked JSONL span; internal offsets are checked too."""
        data = self.opened()
        if _native is not None:
            return _native.span(data, self.index_offset, self.count, start, stop)
        if start >= stop:
            return b""
        first, previous = self.bounds(data, start)
        for position in range(start + 1, stop):
            begin, end = self.bounds(data, position)
            if begin != previous:
                raise _invalid()
            previous = end
        return data[first:previous]

    def verify(self) -> None:
        """Validate all offsets and the body checksum, without decoding JSON."""
        data = self.opened()
        if _native is not None:
            _native.validate(data, self.index_offset, self.count)
        else:
            for position in range(self.count):
                self.bounds(data, position)
        digest = sha256()
        with memoryview(data) as view:
            for start in range(_HEADER.size, len(data), _CHUNK):
                with view[start : start + _CHUNK] as chunk:
                    digest.update(chunk)
        if digest.digest() != self._digest:
            msg = "dataset checksum mismatch"
            raise ValueError(msg)

    def close(self) -> None:
        """Release the mapping; no persistent memoryviews escape this module."""
        if self._data is not None:
            self._data.close()
            self._data = None
