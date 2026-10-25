"""Native inflation and CRC under the interpreter's gzip framing and budgets.

The complete third-party C gzip reader validates trailers before exposing the
final partial buffer. Reuse the interpreter's framing, passing each request
straight to the native inflater and CRC kernel without a property adapter.
"""

from __future__ import annotations

import gzip
import io
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
if _accelerated is not None:
    _factory = _accelerated.Inflater if _modern else _legacy_inflater
    _crc32 = _accelerated.crc32
else:
    _factory = _native._ZlibDecompressor if _modern else _native.decompressobj
    _crc32 = _native.crc32

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
_read = cast("Callable[[gzip._GzipReader, int], bytes]", _read_impl)


def _native_crc_read[**P](fallback: Callable[P, bytes]) -> Callable[P, bytes]:
    """Bind the native implementation directly, retaining fallback translation."""
    return cast("Callable[P, bytes]", _read) if _accelerated is not None else fallback


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
