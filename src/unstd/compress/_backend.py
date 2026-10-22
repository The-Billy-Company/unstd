"""Typed, checked boundaries over zlib-ng's partially annotated modules."""

from __future__ import annotations

import gzip as _stdlib
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from zlib_ng import gzip_ng, zlib_ng


if TYPE_CHECKING:
    from collections.abc import Buffer


@runtime_checkable
class _Native(Protocol):
    error: type[Exception]


@runtime_checkable
class _Gzip(Protocol):
    GzipFile: type[_stdlib.GzipFile]

    def compress(
        self, data: Buffer, compresslevel: int, *, mtime: float | None
    ) -> bytes: ...


def _native(value: object) -> _Native:
    """The codec boundary, including a real exception class."""
    if (
        isinstance(value, _Native)
        and isinstance(value.error, type)
        and issubclass(value.error, Exception)
    ):
        return value
    msg = "zlib-ng does not provide the expected native codec surface"
    raise TypeError(msg)


def _gzip(value: object) -> _Gzip:
    """A native file class that really inherits the stdlib file interface."""
    if (
        isinstance(value, _Gzip)
        and isinstance(value.GzipFile, type)
        and issubclass(value.GzipFile, _stdlib.GzipFile)
    ):
        return value
    msg = "zlib-ng does not provide the expected gzip surface"
    raise TypeError(msg)


native = _native(zlib_ng)
gzip = _gzip(gzip_ng)
