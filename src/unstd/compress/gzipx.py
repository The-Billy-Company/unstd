"""Stdlib gzip calls with optional zlib-ng codecs and faithful stream errors.

The native backend lives behind ``unstd[compress]``. Base installs bind directly
to stdlib ``gzip``. Native streams keep stdlib header/trailer parsing, ownership,
seeking and buffering; only their codecs change. Compressed bytes and ratios may
differ between backends, while decoded bytes and exception families stay faithful.
"""

from __future__ import annotations

from contextlib import suppress
import gzip as _stdlib
import inspect
import io
from os import PathLike
from typing import TYPE_CHECKING, Literal, cast, overload, override
import zlib


if TYPE_CHECKING:
    from collections.abc import Buffer
    from gzip import GzipFile as _Base
    from gzip import _ReadableFileobj, _WritableFileobj
    from typing import Protocol

    from unstd.compress._backend import gzip as _backend
    from unstd.compress._backend import native as _native

    class _Compressor(Protocol):
        def __call__(
            self, data: Buffer, compresslevel: int, *, mtime: float | None
        ) -> bytes: ...

else:
    try:
        from unstd.compress._backend import gzip as _backend
        from unstd.compress._backend import native as _native
    except ImportError:
        _backend = None
    else:
        _Base = _backend.GzipFile


__all__ = ["BadGzipFile", "GzipFile", "compress", "decompress", "open"]
BadGzipFile = _stdlib.BadGzipFile

type _Path = str | bytes | PathLike[str] | PathLike[bytes]
type _BinaryMode = Literal["r", "rb", "w", "wb", "a", "ab", "x", "xb"]
type _TextMode = Literal["rt", "wt", "at", "xt"]


if TYPE_CHECKING or _backend is not None:
    from unstd.compress._inflate import _Reader

    _level: object = inspect.signature(_stdlib.open).parameters["compresslevel"].default
    _mtime: object = inspect.signature(_stdlib.compress).parameters["mtime"].default
    if not isinstance(_level, int) or not (
        _mtime is None or isinstance(_mtime, int | float)
    ):
        _msg = "unsupported stdlib gzip defaults"
        raise TypeError(_msg)

    _DEFAULT_LEVEL: int = _level
    _DEFAULT_MTIME: float | None = _mtime

    class GzipFile(_Base):
        """A gzip binary file with native codecs and stdlib stream semantics."""

        _buffer: io.BufferedReader | io.BufferedWriter

        def __init__(
            self,
            filename: _Path | None = None,
            mode: str | None = None,
            compresslevel: int = _DEFAULT_LEVEL,
            fileobj: _ReadableFileobj | _WritableFileobj | None = None,
            mtime: float | None = None,
        ) -> None:
            """Open a binary stream; explicit file objects remain caller-owned."""
            if fileobj is not None:
                effective_mode: object = (
                    mode if mode is not None else getattr(fileobj, "mode", "rb")
                )
                if isinstance(effective_mode, str):
                    operation = (
                        "read"
                        if effective_mode.startswith("r")
                        else "write"
                        if effective_mode.startswith(("w", "a", "x"))
                        else None
                    )
                    if (
                        operation is not None
                        and "t" not in effective_mode
                        and "U" not in effective_mode
                        and not callable(getattr(fileobj, operation, None))
                    ):
                        msg = f"gzip file object must provide {operation}()"
                        raise TypeError(msg)
            try:
                super().__init__(filename, mode, compresslevel, fileobj, mtime)
                if self.readable():
                    # Failed setup must leave the original buffer open so
                    # stdlib close() still releases an owned source handle.
                    replacement: io.BufferedReader = io.BufferedReader(
                        _Reader(self.fileobj)
                    )
                    self._buffer.close()
                    self._buffer = replacement
            except BaseException as error:
                with suppress(Exception):
                    self.close()
                if isinstance(error, _native.error):
                    raise zlib.error(str(error)) from error
                raise

        @override
        def write(self, data: Buffer) -> int:
            """Write a buffer with native compression and stdlib errors."""
            try:
                return super().write(data)
            except _native.error as exc:
                raise zlib.error(str(exc)) from exc

        @override
        def flush(self, zlib_mode: int = zlib.Z_SYNC_FLUSH) -> None:
            """Flush buffered input and the native compressor."""
            try:
                super().flush(zlib_mode)
            except _native.error as exc:
                raise zlib.error(str(exc)) from exc

        @override
        def close(self) -> None:
            """Finalize a member, closing only handles owned by this file."""
            try:
                super().close()
            except _native.error as exc:
                raise zlib.error(str(exc)) from exc

    @overload
    def open(
        filename: _Path | _ReadableFileobj | _WritableFileobj,
        mode: _BinaryMode = "rb",
        compresslevel: int = _DEFAULT_LEVEL,
        encoding: None = None,
        errors: None = None,
        newline: None = None,
    ) -> GzipFile: ...

    @overload
    def open(
        filename: _Path | _ReadableFileobj | _WritableFileobj,
        mode: _TextMode,
        compresslevel: int = _DEFAULT_LEVEL,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> io.TextIOWrapper: ...

    @overload
    def open(
        filename: _Path | _ReadableFileobj | _WritableFileobj,
        mode: str,
        compresslevel: int = _DEFAULT_LEVEL,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> GzipFile | io.TextIOWrapper: ...

    def open(
        filename: _Path | _ReadableFileobj | _WritableFileobj,
        mode: str = "rb",
        compresslevel: int = _DEFAULT_LEVEL,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> GzipFile | io.TextIOWrapper:
        """Open a path or caller-owned gzip file in binary or text mode."""
        if "t" in mode:
            if "b" in mode:
                msg = f"Invalid mode: {mode!r}"
                raise ValueError(msg)
        elif encoding is not None or errors is not None or newline is not None:
            msg = "encoding, errors and newline are only supported in text mode"
            raise ValueError(msg)
        binary_mode = mode.replace("t", "")
        if isinstance(filename, str | bytes | PathLike):
            binary = GzipFile(filename, binary_mode, compresslevel)
        elif callable(getattr(filename, "read", None)) or callable(
            getattr(filename, "write", None)
        ):
            binary = GzipFile(None, binary_mode, compresslevel, filename)
        else:
            msg = "filename must be a str or bytes object, or a file"
            raise TypeError(msg)
        if "t" in mode:
            try:
                return io.TextIOWrapper(binary, encoding, errors, newline)
            except BaseException:
                with suppress(Exception):
                    binary.close()
                raise
        return binary

    def compress(
        data: Buffer,
        compresslevel: int = _DEFAULT_LEVEL,
        *,
        mtime: float | None = _DEFAULT_MTIME,
    ) -> bytes:
        """Compress a buffer with this interpreter's stdlib level/mtime defaults."""
        try:
            result: bytes = _backend.compress(data, compresslevel, mtime=mtime)
        except _native.error:
            # Python 3.14 retains None for the current time, although its
            # typeshed signature currently permits only numeric timestamps.
            compressor = cast("_Compressor", _stdlib.compress)
            return compressor(memoryview(data), compresslevel, mtime=mtime)
        return result

    def decompress(data: Buffer) -> bytes:
        """Decode concatenated gzip members, with stdlib diagnostics on failure."""
        try:
            result: bytes = _backend.decompress(data)
        except (_native.error, EOFError, BadGzipFile):
            return _stdlib.decompress(data)
        return result

else:
    # Exact bindings keep the dependency-free base path free of wrapper overhead.
    GzipFile = _stdlib.GzipFile
    open = _stdlib.open
    compress = _stdlib.compress
    decompress = _stdlib.decompress
