"""Stdlib-compatible gzip with optional native compression and read kernels.

``unstd[compress]`` enables zlib-ng writes, native bounded inflation and CRC, and
libdeflate decoding for eligible byte buffers. Read framing and buffering follow
the running interpreter's gzip implementation. Base installs bind directly to
stdlib ``gzip``; unsupported buffer cases retain stdlib parsing and diagnostics.
"""

from __future__ import annotations

import builtins
from contextlib import suppress
import gzip as _stdlib
import inspect
import io
from os import PathLike, fspath
from typing import TYPE_CHECKING, Literal, cast, overload, override
import zlib


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable
    from gzip import GzipFile as _Base
    from gzip import _ReadableFileobj, _WritableFileobj
    from typing import IO, Protocol

    from unstd.compress.engines.native import gzip as _backend
    from unstd.compress.engines.native import native as _native
    from unstd.compress.engines.reader import _Reader

    _decode_buffer: Callable[[object], bytes | None] | None

    class _Compressor(Protocol):
        def __call__(
            self, data: Buffer, compresslevel: int, *, mtime: float | None
        ) -> bytes: ...

    class _ReadState(Protocol):
        # The running gzip implementation permits unset/closed handles and
        # byte filenames; typeshed narrows those private fields too far.
        fileobj: _ReadableFileobj | _WritableFileobj | None
        myfileobj: IO[bytes] | None
        name: str | bytes

else:
    try:
        from unstd.compress.engines.native import gzip as _backend
        from unstd.compress.engines.native import native as _native
    except ImportError:
        _backend = None
        _decode_buffer = None
    else:
        _Base = _backend.GzipFile
        from unstd.compress.engines.reader import _Reader

        try:
            from unstd.compress._gzip_native import decompress as _decode_buffer
        except ImportError:
            _decode_buffer = None


__all__ = ["BadGzipFile", "GzipFile", "compress", "decompress", "open"]
BadGzipFile = _stdlib.BadGzipFile

type _Path = str | bytes | PathLike[str] | PathLike[bytes]
type _BinaryMode = Literal["r", "rb", "w", "wb", "a", "ab", "x", "xb"]
type _TextMode = Literal["rt", "wt", "at", "xt"]


if TYPE_CHECKING or _backend is not None:
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
        """Native gzip codecs with the interpreter's framing and file behavior."""

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
            effective_mode: object = (
                mode if mode is not None else getattr(fileobj, "mode", "rb")
            )
            if fileobj is not None and isinstance(effective_mode, str):
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
            if (
                isinstance(effective_mode, str)
                and effective_mode.startswith("r")
                and mode
                and ("t" in mode or "U" in mode)
            ):
                msg = f"Invalid mode: {mode!r}"
                raise ValueError(msg)
            try:
                if isinstance(effective_mode, str) and effective_mode.startswith("r"):
                    # Initialize only the read branch. The backend constructor
                    # first creates a stdlib reader and then replaces it.
                    state = cast("_ReadState", self)
                    state.fileobj = None
                    binary_mode = mode + "b" if mode and "b" not in mode else mode
                    if fileobj is None:
                        opened = builtins.open(  # noqa: SIM115, PTH123 — owned gzip handle and bytes paths
                            cast("_Path", filename), binary_mode or "rb"
                        )
                        state.myfileobj = opened
                        fileobj = opened
                    if filename is None:
                        name = getattr(fileobj, "name", "")
                        if not isinstance(name, str | bytes):
                            name = ""
                    else:
                        name = fspath(filename)
                    self.mode = _stdlib.READ
                    raw = _Reader(cast("_ReadableFileobj", fileobj))
                    self._buffer = io.BufferedReader(raw)
                    state.name = name
                    state.fileobj = fileobj
                else:
                    super().__init__(filename, mode, compresslevel, fileobj, mtime)
            except BaseException as error:
                with suppress(Exception):
                    self.close()
                # Older stdlib constructors can fail before assigning fileobj,
                # leaving close() unable to release an already-owned handle.
                owned = self.myfileobj
                self.myfileobj = None
                if owned is not None:
                    with suppress(Exception):
                        owned.close()
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
        """Decode validated buffers natively; preserve other stdlib outcomes."""
        # gzip slices its input by element; non-byte views and subclasses can
        # give those slices different meanings from the C buffer protocol.
        if type(data) is not bytes and type(data) is not bytearray:
            return _stdlib.decompress(data)
        if _decode_buffer is not None:
            result = _decode_buffer(data)
            if result is not None:
                return result
        # Concatenations and truncation retain gzip's exact behavior.
        # Its parser also accepts reserved flags and ignores header checksums
        # that the C decoder rejects; codec errors above take the same fallback.
        return _stdlib.decompress(data)

else:
    # Exact bindings keep the dependency-free base path free of wrapper overhead.
    GzipFile = _stdlib.GzipFile
    open = _stdlib.open
    compress = _stdlib.compress
    decompress = _stdlib.decompress
