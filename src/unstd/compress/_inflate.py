"""Native inflation under stdlib gzip's header, trailer and stream machinery."""

from __future__ import annotations

import gzip
import io
from typing import TYPE_CHECKING, Protocol, runtime_checkable
import zlib

from unstd.compress._backend import native as _native


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable


@runtime_checkable
class _Readable(Protocol):
    def read(self, size: int, /) -> bytes: ...


@runtime_checkable
class _Seekable(Protocol):
    def seek(self, offset: int, /) -> object: ...


class _Source:
    """A typed read/seek boundary; sequential sources need not implement seek."""

    def __init__(self, source: object) -> None:
        if not isinstance(source, _Readable) or not callable(source.read):
            msg = "gzip input must provide read(size) returning bytes"
            raise TypeError(msg)
        self._source = source

    def read(self, size: int, /) -> bytes:
        return self._source.read(size)

    def seek(self, offset: int, /) -> object:
        if isinstance(self._source, _Seekable):
            return self._source.seek(offset)
        msg = "gzip source does not support seeking"
        raise io.UnsupportedOperation(msg)


@runtime_checkable
class _Inflation(Protocol):
    def decompress(self, data: Buffer, /, max_length: int) -> bytes: ...

    @property
    def unused_data(self) -> bytes: ...

    @property
    def eof(self) -> bool: ...


@runtime_checkable
class _NeedsInput(Protocol):
    @property
    def needs_input(self) -> bool: ...


@runtime_checkable
class _UnconsumedTail(Protocol):
    @property
    def unconsumed_tail(self) -> bytes: ...


class _Inflater:
    """One native inflater, with stdlib errors and either CPython state surface."""

    def __init__(self, raw: object) -> None:
        if not isinstance(raw, _Inflation):
            msg = "native backend does not provide a compatible inflater"
            raise TypeError(msg)
        self._raw = raw

    def decompress(self, data: Buffer, /, max_length: int) -> bytes:
        try:
            return self._raw.decompress(data, max_length)
        except _native.error as exc:
            raise zlib.error(str(exc)) from exc

    @property
    def unused_data(self) -> bytes:
        return self._raw.unused_data

    @property
    def eof(self) -> bool:
        return self._raw.eof

    @property
    def needs_input(self) -> bool:
        if isinstance(self._raw, _NeedsInput):
            return self._raw.needs_input
        msg = "this inflater uses unconsumed_tail instead of needs_input"
        raise TypeError(msg)

    @property
    def unconsumed_tail(self) -> bytes:
        if isinstance(self._raw, _UnconsumedTail):
            return self._raw.unconsumed_tail
        msg = "this inflater uses needs_input instead of unconsumed_tail"
        raise TypeError(msg)


class _Reader(gzip._GzipReader):
    """Stdlib framing with a native inflater selected by runtime capability."""

    _decompressor: object
    _decomp_factory: Callable[..., _Inflater]

    def __init__(self, source: object) -> None:
        super().__init__(_Source(source))
        # CPython backported needs_input to some maintenance releases. Probe
        # the actual stdlib inflater, rather than guessing from the minor version.
        self._modern = isinstance(self._decompressor, _NeedsInput)
        self._decomp_factory = self._inflater
        self._decompressor = self._inflater(wbits=-zlib.MAX_WBITS)

    def _inflater(self, *, wbits: int) -> _Inflater:
        raw: object = (
            _native._ZlibDecompressor(wbits=wbits)
            if self._modern
            else _native.decompressobj(wbits)
        )
        return _Inflater(raw)
