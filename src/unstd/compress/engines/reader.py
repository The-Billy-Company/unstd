"""Native inflation and CRC under the interpreter's gzip framing and budgets.

The complete third-party C gzip reader validates trailers before exposing the
final partial buffer. Reuse the interpreter's framing, passing each request
straight to the native inflater and CRC kernel without a property adapter.
"""

from __future__ import annotations

import gzip
import io
import platform
import sys
from types import FunctionType, SimpleNamespace
from typing import TYPE_CHECKING, cast, override
import zlib

from zlib_ng import zlib_ng as _native

from unstd.compress.engines.native import native as _codec


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable
    from gzip import _ReadableFileobj
    from typing import Protocol

    class _Inflation(Protocol):
        def decompress(self, data: Buffer, /, max_length: int = -1) -> bytes: ...

        @property
        def unused_data(self) -> bytes: ...

        @property
        def eof(self) -> bool: ...

    class _InflaterFactory(Protocol):
        def __call__(self, *, wbits: int) -> _Inflation: ...

    class _NativeFactory(Protocol):
        def __call__(self, wbits: int = -15, *, legacy: bool = False) -> _Inflation: ...

    class _NativeSurface(Protocol):
        @property
        def Inflater(self) -> _NativeFactory: ...  # noqa: N802 — native export

        def crc32(self, data: Buffer, value: int = 0, /) -> int: ...

    class _RawReader(Protocol):
        @property
        def _decompressor(self) -> object: ...

    class _BufferedInflation(_Inflation, Protocol):
        @property
        def needs_input(self) -> bool: ...

    class _BufferedFactory(Protocol):
        def __call__(self, *, wbits: int) -> _BufferedInflation: ...

    class _CompressedInput(Protocol):
        def read(self, size: int = -1) -> bytes: ...

        def prepend(self, data: bytes) -> None: ...

    class _FramedReader(Protocol):
        _decompressor: _BufferedInflation
        _decomp_factory: _BufferedFactory
        _decomp_args: dict[str, int]
        _fp: _CompressedInput
        _new_member: bool
        _size: int
        _pos: int
        _crc: int
        _stream_size: int

        def readall(self) -> bytes: ...

        def _read_eof(self) -> None: ...

        def _init_read(self) -> None: ...

        def _read_gzip_header(self) -> bool: ...


def _load_native() -> _NativeSurface | None:
    """Use the bundled read kernels when built; retain the portable backend."""
    try:
        from unstd.compress import _gzip_native
    except ImportError:
        return None
    return _gzip_native


_accelerated = _load_native()


def _legacy_inflater(*, wbits: int) -> _Inflation:
    """Select unconsumed_tail semantics on older CPython maintenance releases."""
    surface = _accelerated
    if surface is None:
        msg = "native inflater is unavailable"
        raise RuntimeError(msg)
    return surface.Inflater(wbits=wbits, legacy=True)


# Maintenance releases backported needs_input independently of minor versions.
# Inspect the real choice once, outside stream setup, without an adapter per read.
with gzip._GzipReader(io.BytesIO()) as _probe:
    _modern = hasattr(cast("_RawReader", _probe)._decompressor, "needs_input")

_factory: _InflaterFactory
# Apple libz wins the qualified macOS dataset/upload workloads. Linux retains
# zlib-ng for small or fragmented reads and admits private ISA-L on supported
# CPUs for large reads. All routes retain this interpreter's framing and budgets.
_isal_streaming = bool(getattr(_accelerated, "ISAL_STREAMING", False))
if _accelerated is not None and (
    sys.platform != "linux" or (_modern and _isal_streaming)
):
    _factory = _accelerated.Inflater if _modern else _legacy_inflater
    _bundled_inflater = True
else:
    _factory = _native._ZlibDecompressor if _modern else _native.decompressobj
    _bundled_inflater = False
_crc32 = _accelerated.crc32 if _accelerated is not None else _native.crc32

# Bind the already imported provider once. The C bridge selects an engine only
# before a member's first native decode; framing and per-read budgets stay here.
_adaptive_ng_version: str | None = None
if (
    _bundled_inflater
    and _modern
    and (
        sys.platform == "linux"
        or (sys.platform == "darwin" and platform.machine() in {"arm64", "aarch64"})
    )
):
    _configure_ng = getattr(_accelerated, "_configure_ng", None)
    _ng_version = getattr(_accelerated, "_ng_runtime_version", None)
    _ng_path = getattr(_native, "__file__", None)
    if (
        callable(_configure_ng)
        and callable(_ng_version)
        and isinstance(_ng_path, str)
        and _configure_ng(_ng_path)
    ):
        _adaptive_ng_version = _ng_version()
    elif sys.platform == "linux":
        _factory = _native._ZlibDecompressor if _modern else _native.decompressobj
        _bundled_inflater = False

# Clone the running interpreter's read implementation with a private CRC binding.
# Its framing, input budgets and error-delivery order stay version faithful;
# neither the stdlib module nor its function globals are modified.
_read_globals: dict[str, object] = dict(gzip._GzipReader.read.__globals__)
_zlib_globals: dict[str, object] = dict(vars(zlib))
_zlib_globals["crc32"] = _crc32
_read_globals["zlib"] = SimpleNamespace(**_zlib_globals)
_read_impl = FunctionType(
    gzip._GzipReader.read.__code__,
    _read_globals,
    gzip._GzipReader.read.__name__,
    gzip._GzipReader.read.__defaults__,
    gzip._GzipReader.read.__closure__,
)
_read_impl.__kwdefaults__ = gzip._GzipReader.read.__kwdefaults__
_read_impl.__annotations__ = gzip._GzipReader.read.__annotations__.copy()
_read_buffer_size = cast(
    "int", _read_globals.get("READ_BUFFER_SIZE", io.DEFAULT_BUFFER_SIZE)
)


def _read_buffered(self: _FramedReader, size: int = -1) -> bytes:
    """Retain gzip framing while draining buffered input before source EOF."""
    if size < 0:
        return self.readall()
    if not size:
        return b""
    while True:
        if self._decompressor.eof:
            self._read_eof()
            self._new_member = True
            self._decompressor = self._decomp_factory(**self._decomp_args)
        if self._new_member:
            self._init_read()
            if not self._read_gzip_header():
                self._size = self._pos
                return b""
            self._new_member = False
        needs_input = self._decompressor.needs_input
        incoming = self._fp.read(_read_buffer_size) if needs_input else b""
        uncompress = self._decompressor.decompress(incoming, size)
        if self._decompressor.unused_data != b"":
            self._fp.prepend(self._decompressor.unused_data)
        if uncompress != b"":
            break
        # A buffered drain can finish without output and then request input.
        # Only an actual empty source read establishes a truncated member.
        if needs_input and incoming == b"":
            message = (
                "Compressed file ended before the end-of-stream marker was reached"
            )
            raise EOFError(message)
    self._crc = _crc32(uncompress, self._crc)
    self._stream_size += len(uncompress)
    self._pos += len(uncompress)
    return uncompress


_read = cast(
    "Callable[[gzip._GzipReader, int], bytes]",
    # ISA-L can expose an empty drain that the 3.12 needs_input backport
    # leaves uninitialized, and newer stdlib readers mistake for source EOF.
    _read_buffered if _modern and _isal_streaming else _read_impl,
)


def _native_crc_read[**P](fallback: Callable[P, bytes]) -> Callable[P, bytes]:
    """Bind the native implementation directly, retaining fallback translation."""
    return cast("Callable[P, bytes]", _read) if _bundled_inflater else fallback


class _Reader(gzip._GzipReader):
    """One native inflater per member, with stdlib framing and error families."""

    def __init__(self, source: _ReadableFileobj) -> None:
        super(gzip._GzipReader, self).__init__(
            gzip._PaddedFile(source), _factory, wbits=-zlib.MAX_WBITS
        )
        self._new_member = True
        self._last_mtime = None

    def _init_read(self) -> None:
        """Start a member with the defined CRC32 and size identities."""
        self._crc = 0
        self._stream_size = 0

    @override
    @_native_crc_read
    def read(self, size: int = -1) -> bytes:
        """Inflate directly; translate fallback codec errors at the boundary."""
        try:
            return _read(self, size)
        except _codec.error as error:
            raise zlib.error(str(error)) from error
