"""Bounded JSON-array output using the existing per-record transport encoder."""

from __future__ import annotations

import errno
from functools import partial
import io
from typing import TYPE_CHECKING

from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from unstd.serde.jsonx import _BinaryWriter


def _write_all[T: (str, bytes)](
    write: Callable[[T], object], data: T, require_count: bool
) -> None:
    """Finish one bounded chunk before advancing the producer or writing again."""
    while data:
        written = write(data)
        if written is None and not require_count:
            return
        if written is None or (type(written) is int and written == 0):
            message = "JSON array sink made no write progress"
            raise BlockingIOError(errno.EAGAIN, message)
        if type(written) is not int or not 0 < written <= len(data):
            message = "JSON array sink returned an invalid write count"
            raise OSError(message)
        data = data[written:]


def _emit[T: (str, bytes)](
    rows: Iterable[object],
    write: Callable[[T], object],
    encode: Callable[[object], T],
    empty: T,
    opening: T,
    closing: T,
    separator: T,
    size: int,
    require_count: bool,
) -> int:
    """Batch encoded snapshots without retaining producer-owned mutable rows."""
    _write_all(write, opening, require_count)
    pieces: list[T] = []
    buffered = count = 0
    for row in rows:
        encoded = encode(row)
        needed = len(encoded) + bool(count)
        if needed < size - buffered:
            # Most records fit the active batch. Keep that path to list
            # appends; only boundary records need the slicing loop below.
            if count:
                pieces.append(separator)
            pieces.append(encoded)
            buffered += needed
            count += 1
            del row, encoded
            continue
        # Slice complete Unicode records for text sinks: byte boundaries can
        # bisect a UTF-8 code point and cannot be decoded independently.
        for part in (separator, encoded) if count else (encoded,):
            offset = 0
            while offset < len(part):
                stop = offset + min(size - buffered, len(part) - offset)
                pieces.append(part[offset:stop])
                buffered += stop - offset
                offset = stop
                if buffered == size:
                    _write_all(write, empty.join(pieces), require_count)
                    pieces.clear()
                    buffered = 0
        count += 1
        del row, encoded, part
    # A full batch was already emitted, so this last byte/character still fits
    # within the requested bound. Never close the array on an earlier failure.
    pieces.append(closing)
    _write_all(write, empty.join(pieces), require_count)
    return count


def write_array(
    rows: Iterable[object],
    fp: io.TextIOBase | _BinaryWriter,
    size: int,
    sort_keys: bool,
    default: Callable[[object], object] | None,
    numpy: bool,
) -> int:
    """Bind the encoder once and select Unicode or UTF-8 sink writes."""
    encode = jsonx.dumpb
    if sort_keys is not False or default is not None or numpy is not False:
        encode = partial(encode, sort_keys=sort_keys, default=default, numpy=numpy)
    require_count = isinstance(fp, io.IOBase)
    if isinstance(fp, io.TextIOBase):
        return _emit(
            rows,
            fp.write,
            lambda row: encode(row).decode(),
            "",
            "[",
            "]",
            ",",
            size,
            require_count,
        )
    return _emit(rows, fp.write, encode, b"", b"[", b"]", b",", size, require_count)
