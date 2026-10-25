"""The zlib wire/state API with optional zlib-ng acceleration.

The ``compress`` extra selects zlib-ng for complete buffers, incremental
compression and checksums. Incremental decompression retains the running
interpreter's exact C state objects and their EOF/flush behavior. Compressed
bytes may differ between
codecs; recovered bytes, output budgets and stream state keep zlib's contracts.
Native codec errors become the running interpreter's ``zlib.error`` without
replaying a stateful operation. A base install binds directly to stdlib zlib.
"""

from __future__ import annotations

from collections.abc import Buffer  # noqa: TC003 — public runtime type introspection
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast, override, runtime_checkable
import zlib as _stdlib

from unstd.compress._validation import flush_mode as _flush_mode


__all__ = [
    "DEFLATED",
    "DEF_BUF_SIZE",
    "DEF_MEM_LEVEL",
    "MAX_WBITS",
    "ZLIB_RUNTIME_VERSION",
    "ZLIB_VERSION",
    "Z_BEST_COMPRESSION",
    "Z_BEST_SPEED",
    "Z_BLOCK",
    "Z_DEFAULT_COMPRESSION",
    "Z_DEFAULT_STRATEGY",
    "Z_FILTERED",
    "Z_FINISH",
    "Z_FIXED",
    "Z_FULL_FLUSH",
    "Z_HUFFMAN_ONLY",
    "Z_NO_COMPRESSION",
    "Z_NO_FLUSH",
    "Z_PARTIAL_FLUSH",
    "Z_RLE",
    "Z_SYNC_FLUSH",
    "Z_TREES",
    "adler32",
    "compress",
    "compressobj",
    "crc32",
    "decompress",
    "decompressobj",
    "error",
]

DEFLATED = _stdlib.DEFLATED
DEF_BUF_SIZE = _stdlib.DEF_BUF_SIZE
DEF_MEM_LEVEL = _stdlib.DEF_MEM_LEVEL
MAX_WBITS = _stdlib.MAX_WBITS
Z_BEST_COMPRESSION = _stdlib.Z_BEST_COMPRESSION
Z_BEST_SPEED = _stdlib.Z_BEST_SPEED
Z_BLOCK = _stdlib.Z_BLOCK
Z_DEFAULT_COMPRESSION = _stdlib.Z_DEFAULT_COMPRESSION
Z_DEFAULT_STRATEGY = _stdlib.Z_DEFAULT_STRATEGY
Z_FILTERED = _stdlib.Z_FILTERED
Z_FINISH = _stdlib.Z_FINISH
Z_FIXED = _stdlib.Z_FIXED
Z_FULL_FLUSH = _stdlib.Z_FULL_FLUSH
Z_HUFFMAN_ONLY = _stdlib.Z_HUFFMAN_ONLY
Z_NO_COMPRESSION = _stdlib.Z_NO_COMPRESSION
Z_NO_FLUSH = _stdlib.Z_NO_FLUSH
Z_PARTIAL_FLUSH = _stdlib.Z_PARTIAL_FLUSH
Z_RLE = _stdlib.Z_RLE
Z_SYNC_FLUSH = _stdlib.Z_SYNC_FLUSH
Z_TREES = _stdlib.Z_TREES
ZLIB_RUNTIME_VERSION = _stdlib.ZLIB_RUNTIME_VERSION
ZLIB_VERSION = _stdlib.ZLIB_VERSION
error = _stdlib.error


class _Compression(Protocol):
    def compress(self, data: Buffer, /) -> bytes: ...
    def flush(self, mode: int = Z_FINISH, /) -> bytes: ...
    def copy(self) -> _Compression: ...


@runtime_checkable
class _Codec(Protocol):
    error: type[Exception]
    ZLIB_VERSION: str
    ZLIB_RUNTIME_VERSION: str

    def compress(
        self, data: Buffer, /, level: int = -1, wbits: int = MAX_WBITS
    ) -> bytes: ...

    def decompress(
        self,
        data: Buffer,
        /,
        wbits: int = MAX_WBITS,
        bufsize: int = DEF_BUF_SIZE,
    ) -> bytes: ...

    def compressobj(
        self,
        level: int,
        method: int,
        wbits: int,
        memLevel: int,  # noqa: N803 — zlib's public keyword
        strategy: int,
        zdict: Buffer = ...,
    ) -> _Compression: ...

    def crc32(self, data: Buffer, value: int = 0, /) -> int: ...
    def adler32(self, data: Buffer, value: int = 1, /) -> int: ...


def _load_native() -> _Codec | None:
    """Admit the provider's typed surface once, retaining a stdlib fallback."""
    try:
        value = import_module("zlib_ng.zlib_ng")
    except ImportError:
        return None
    if (
        isinstance(value, _Codec)
        and isinstance(value.error, type)
        and issubclass(value.error, Exception)
        and isinstance(value.ZLIB_VERSION, str)
        and isinstance(value.ZLIB_RUNTIME_VERSION, str)
    ):
        return value
    return None


class _Omitted:
    """The omitted dictionary has different semantics from an empty dictionary."""

    __slots__ = ()

    @override
    def __repr__(self) -> str:
        """A stable signature display for zlib's unrepresentable default."""
        return "<omitted>"


_OMITTED = _Omitted()
_backend = _load_native()
if TYPE_CHECKING or _backend is not None:
    _native = cast("_Codec", _backend)
    ZLIB_VERSION = _native.ZLIB_VERSION
    ZLIB_RUNTIME_VERSION = _native.ZLIB_RUNTIME_VERSION

    class _Compress:
        """A native compressor state with stdlib exception families."""

        __slots__ = ("_impl",)

        def __init__(self, implementation: _Compression) -> None:
            """Own the one native state; failed calls are never replayed."""
            self._impl = implementation

        def compress(self, data: Buffer, /) -> bytes:
            """Compress one input buffer into the existing stream."""
            try:
                return self._impl.compress(data)
            except _native.error as exc:
                raise error(str(exc)) from exc

        def flush(self, mode: int = Z_FINISH, /) -> bytes:
            """Flush with zlib's mode and terminal-state behavior."""
            mode = _flush_mode(mode)
            try:
                return self._impl.flush(mode)
            except _native.error as exc:
                raise error(str(exc)) from exc

        def copy(self) -> _Compress:
            """An independent native copy at the current stream position."""
            try:
                return _Compress(self._impl.copy())
            except _native.error as exc:
                raise error(str(exc)) from exc

        def __copy__(self) -> _Compress:
            """The standard shallow-copy protocol retains independent state."""
            return self.copy()

        def __deepcopy__(self, memo: dict[int, object]) -> _Compress:
            """The standard deep-copy protocol copies the codec's native state."""
            return self.copy()

    def compress(data: Buffer, /, level: int = -1, wbits: int = MAX_WBITS) -> bytes:
        """Compress one buffer into a zlib, raw DEFLATE or gzip stream."""
        try:
            return _native.compress(data, level, wbits)
        except _native.error as exc:
            raise error(str(exc)) from exc

    def decompress(
        data: Buffer, /, wbits: int = MAX_WBITS, bufsize: int = DEF_BUF_SIZE
    ) -> bytes:
        """Decode one complete stream with zlib's format and buffer defaults."""
        try:
            return _native.decompress(data, wbits, bufsize)
        except _native.error as exc:
            raise error(str(exc)) from exc

    def compressobj(
        level: int = -1,
        method: int = DEFLATED,
        wbits: int = MAX_WBITS,
        memLevel: int = DEF_MEM_LEVEL,  # noqa: N803 — zlib's public keyword
        strategy: int = Z_DEFAULT_STRATEGY,
        zdict: Buffer | _Omitted = _OMITTED,
    ) -> _Compress:
        """Create one incremental compressor; dictionaries remain opt-in."""
        try:
            if zdict is _OMITTED:
                implementation = _native.compressobj(
                    level, method, wbits, memLevel, strategy
                )
            else:
                implementation = _native.compressobj(
                    level, method, wbits, memLevel, strategy, cast("Buffer", zdict)
                )
        except _native.error as exc:
            raise error(str(exc)) from exc
        return _Compress(implementation)

    # Checksums have no codec-error result. Their argument/buffer failures
    # already use the stdlib exception classes, so retain direct C dispatch.
    crc32 = _native.crc32
    adler32 = _native.adler32

else:
    compress = _stdlib.compress
    decompress = _stdlib.decompress
    compressobj = _stdlib.compressobj
    crc32 = _stdlib.crc32
    adler32 = _stdlib.adler32


# The provider keeps older CPython EOF/flush tail behavior. Use this
# interpreter's exact C state object instead of reconstructing opaque state.
decompressobj = _stdlib.decompressobj


# New interpreter APIs retain their exact stdlib implementation until their
# backend behavior has an independent compatibility contract.
for _name in dir(_stdlib):
    if not _name.startswith("_") and _name not in __all__:
        globals()[_name] = getattr(_stdlib, _name)
        __all__ += [_name]
