"""Exact stdlib JSON semantics with guarded native acceleration.

``dumps`` uses orjson only for exact builtin trees whose output matches
stdlib byte-for-byte. The optional native guard proves eligibility and encodes
without releasing the GIL or invoking Python callbacks. Its portable fallback
uses private snapshots. Wide trees and two-space indentation use native proofs
with bounded depth. Unicode output retains stdlib ASCII escaping. Indented
finite floats on Python 3.12 and eligible finite-float trees use CPython's exact
number spellings. Unsupported values, complex formatting and user callbacks
retain their stdlib paths.
Common builtin option configurations reuse stateless stdlib JSONEncoder
instances, retaining CPython's native ``_json`` implementation for all values.

``loads`` and ``load`` use msgspec with exact immutable inputs and no custom
options. Its arbitrary-precision integers and correctly rounded floats preserve
stdlib numbers without a second tree walk. Backend errors use the original
document with stdlib, preserving extensions, integer limits and diagnostics.
Validation retains stdlib's Python recursion headroom. ``dump`` delegates
streaming writes to stdlib.

``JSONEncoder``, ``JSONDecoder`` and ``JSONDecodeError`` remain the original
stdlib classes. Default escaping, spacing, values, subclasses, key behavior,
callbacks, interpreter limits and errors follow the running stdlib. ``dumpb``
is exactly compact ``ensure_ascii=False`` stdlib JSON encoded as UTF-8;
``canonical`` retains the package's sorted, ASCII-escaped identity format.

The existing ``numpy=True`` extension converts arrays with ``.tolist()`` and
scalars with ``.item()``. Custom ``cls`` receives all extra constructor keywords,
including ``numpy``, unchanged.

Contract sources: https://docs.python.org/3.12/library/json.html,
https://github.com/python/cpython/tree/3.12/Lib/json, and
https://github.com/ijl/orjson. Differential and generated tests exercise both
successful acceleration and every fallback boundary against the running stdlib.
"""

from __future__ import annotations

import codecs
from collections.abc import (  # noqa: TC003 — public annotation introspection
    Callable,
    Iterable,
    Iterator,
)
from functools import wraps
import io  # noqa: TC003 — public annotation introspection
import json as _stdlib
from math import isfinite
from operator import index
from typing import TYPE_CHECKING, Any, Protocol, cast

from unstd.serde.encoding import (
    _compact_bytes,
    _encoder,
    _exact_bytes,
    _exact_encode,
    _format,
    _native,
)
from unstd.serde.recursion import _check_headroom


class _TextWriter(Protocol):
    """A text sink accepted by stdlib's streaming JSON encoder."""

    def write(self, text: str, /) -> object:
        """Write one encoder chunk."""
        ...


class _Reader(Protocol):
    """A text or binary source accepted by JSON loading."""

    def read(self) -> str | bytes | bytearray:
        """Read the complete document once."""
        ...


class _BinaryWriter(Protocol):
    """A binary sink for bounded JSON-array writes."""

    def write(self, data: bytes, /) -> object:
        """Write a prefix, returning its length or acknowledging it with None."""
        ...


class _StreamReader(Protocol):
    """A text or binary source supporting bounded reads."""

    def read(self, size: int, /) -> str | bytes | bytearray:
        """Read at most size characters or bytes; an empty result means EOF."""
        ...


if TYPE_CHECKING:
    from types import NotImplementedType

    import msgspec
    import numpy as np
    import orjson

    _HAVE_ORJSON = _HAVE_MSGSPEC = _HAVE_NUMPY = True
else:
    try:
        import orjson
    except ImportError:  # base install without the `serde` extra — stdlib fallback
        orjson = None
    try:
        import msgspec
    except ImportError:
        msgspec = None
    try:
        import numpy as np
    except ImportError:  # numpy is not part of the `serde` extra — opt-in only
        np = None
    _HAVE_ORJSON, _HAVE_MSGSPEC, _HAVE_NUMPY = (
        backend is not None for backend in (orjson, msgspec, np)
    )


JSONDecodeError = _stdlib.JSONDecodeError
JSONDecoder = _stdlib.JSONDecoder
JSONEncoder = _stdlib.JSONEncoder

# The recursive shape of JSON values before any custom decoding hooks run.
type Json = dict[str, Json] | list[Json] | str | int | float | bool | None

__all__ = [
    "JSONDecodeError",
    "JSONDecoder",
    "JSONEncoder",
    "Json",
    "canonical",
    "canonical_strict",
    "dump",
    "dump_array",
    "dumpb",
    "dumps",
    "iterload",
    "load",
    "loads",
]

# orjson option bits — zero when the fast backend is absent, so the fast-path
# arithmetic below stays branch-free either way.
if _HAVE_ORJSON:
    _encode = orjson.dumps
    _SORT = orjson.OPT_SORT_KEYS
    _INDENT = orjson.OPT_INDENT_2
    _NEWLINE = orjson.OPT_APPEND_NEWLINE
    # Proof failures that leave stdlib to decide on the original object.
    _ENCODE_ERRORS = (orjson.JSONEncodeError, RecursionError, RuntimeError)
else:
    _SORT = _INDENT = _NEWLINE = 0
if _HAVE_MSGSPEC:
    # The native parser constructs exact integers and correctly rounded floats.
    _decode = msgspec.json.Decoder().decode
_NO_NUMPY = "numpy=True requires numpy to be installed"
_DEFAULT_ENCODER = _stdlib.JSONEncoder()
_native_dumpb: (
    Callable[[object, bool, object, bool], bytes | NotImplementedType] | None
) = _native.dumpb if _native else None
_native_line: Callable[[object], bytes | NotImplementedType] | None = (
    _native.line if _native else None
)
_native_identity_snapshot: Callable[[object], Json | NotImplementedType] | None = (
    getattr(_native, "identity_snapshot", None) if _native else None
)


def _numpy_default(
    default: Callable[[object], object] | None,
) -> Callable[[object], object]:
    """Wrap *default* so numpy values degrade to JSON-native ones first.

    An ndarray/numpy scalar becomes a JSON-native value before falling through
    to whatever the caller already supplied.

    The extension uses the same conversion regardless of installed backends.
    """
    if not _HAVE_NUMPY:
        raise TypeError(_NO_NUMPY)

    def _default(o: object) -> object:
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, np.generic):
            return o.item()
        if default is not None:
            return default(o)
        msg = f"Object of type {type(o).__name__} is not JSON serializable"
        raise TypeError(msg)

    return _default


def dumps(
    obj: object,
    *,
    skipkeys: bool = False,
    ensure_ascii: bool = True,
    check_circular: bool = True,
    allow_nan: bool = True,
    cls: type[JSONEncoder] | None = None,
    indent: int | str | None = None,
    separators: tuple[str, str] | None = None,
    default: Callable[[Any], Any] | None = None,
    sort_keys: bool = False,
    **kw: Any,
) -> str:
    """Serialize ``obj`` with stdlib formatting, values, hooks and errors.

    The optional ``numpy=True`` extension converts arrays with ``.tolist()`` and
    scalars with ``.item()``, then defers to the user's ``default`` for other
    objects. It requires numpy, which is not part of the ``serde`` extra. With
    an explicit ``cls``, all extra keywords, including ``numpy``, are forwarded
    unchanged to that encoder, as in stdlib.
    """
    if cls is None and kw and kw.pop("numpy", False):
        default = _numpy_default(default)
    encoder, style = None, -1
    if cls is None and default is None and not kw:
        if (
            skipkeys is False
            and ensure_ascii is True
            and check_circular is True
            and allow_nan is True
            and sort_keys is False
            and indent is None
            and separators is None
        ):
            # Default calls need neither format classification nor a cache lookup.
            encoder, style = _DEFAULT_ENCODER, 1
        elif (
            type(skipkeys)
            is type(ensure_ascii)
            is type(check_circular)
            is type(allow_nan)
            is type(sort_keys)
            is bool
        ):
            style = _format(indent, separators)
    if style < 0:
        return _stdlib.dumps(
            obj,
            skipkeys=skipkeys,
            ensure_ascii=ensure_ascii,
            check_circular=check_circular,
            allow_nan=allow_nan,
            cls=cls,
            indent=indent,
            separators=separators,
            default=default,
            sort_keys=sort_keys,
            **kw,
        )
    if _HAVE_ORJSON and style < 3 and type(obj) is not str:
        option = (_SORT if sort_keys else 0) | (_INDENT if style == 2 else 0)
        try:
            result = _exact_encode(obj, ensure_ascii, style, option, _encode)
            if result is not None:
                return result
        except _ENCODE_ERRORS:
            # No callbacks have run; stdlib decides errors on the original
            # object, including a dict changed during the snapshot walk.
            pass
    if encoder is None:
        encoder = _encoder(
            skipkeys,
            ensure_ascii,
            check_circular,
            allow_nan,
            sort_keys,
            indent,
            separators,
        )
    return encoder.encode(obj)


def dumpb(
    obj: object,
    *,
    sort_keys: bool = False,
    default: Callable[[object], object] | None = None,
    numpy: bool = False,
) -> bytes:
    """Serialize bytes identical to compact, unescaped stdlib JSON's UTF-8.

    This extension uses ``ensure_ascii=False`` and compact separators. Native
    encoding handles proven exact builtin trees and eligible finite-float trees;
    other values, ``default`` and ``numpy=True`` use stdlib. For the package's identity format use :func:`canonical`.
    """
    if _native_dumpb is not None:
        native = _native_dumpb(obj, sort_keys, default, numpy)
        if native is not NotImplemented:
            return native
    if numpy and not _HAVE_NUMPY:
        raise TypeError(_NO_NUMPY)
    if not numpy and default is None and type(sort_keys) is bool:
        if _HAVE_ORJSON:
            try:
                result = _exact_bytes(obj, _SORT if sort_keys else 0, _encode)
                if result is not None:
                    return result
            except _ENCODE_ERRORS:
                # Lone surrogates and a low Python recursion limit take stdlib's
                # path. No user callback has run during this eligibility probe.
                pass
        if numpy is False:
            # The native proof has already failed. Reusing the stdlib encoder here
            # avoids copying and proving the same tree again through dumps().
            return _compact_bytes(obj, sort_keys)
    return dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
        default=default,
        numpy=numpy,
    ).encode()


def _line(obj: object) -> bytes:
    """One newline-terminated compact record for :mod:`unstd.serde.ndjson`."""
    if _native_line is not None and (native := _native_line(obj)) is not NotImplemented:
        return native
    if _HAVE_ORJSON:
        try:
            if (result := _exact_bytes(obj, _NEWLINE, _encode)) is not None:
                return result
        except _ENCODE_ERRORS:
            pass
    return _compact_bytes(obj, False) + b"\n"


def canonical(
    obj: object, *, default: Callable[[object], object] | None = str
) -> bytes:
    """The legacy sorted, compact, ASCII-escaped JSON byte format.

    Sorted keys, compact separators and ASCII escaping are independent of the
    installed accelerator. The format retains stdlib number spellings and is
    not RFC 8785/JCS. Existing identity inputs retain their historical bytes.

    The default ``str`` conversion is retained for compatibility with existing
    persisted bytes. Unsupported values can stringify nondeterministically or
    collide with strings. Use :func:`canonical_strict` for new identity inputs;
    use :func:`dumps`/:func:`dumpb` for transport.
    """
    if default is str:
        return _CANONICAL.encode(obj).encode()
    return _stdlib.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=default
    ).encode()


def _identity_snapshot(obj: object, active: set[int]) -> Json:
    """Own an exact JSON tree, rejecting coercions and cycles before encoding."""
    if type(obj) is str:
        obj.encode()  # Reject surrogate code points, including explicit pairs.
        return obj
    if obj is None or type(obj) is int or type(obj) is bool:
        return obj
    if type(obj) is float:
        if isfinite(obj):
            return obj
        msg = "identity JSON requires finite numbers"
        raise ValueError(msg)
    if type(obj) is not list and type(obj) is not dict:
        msg = f"unsupported identity JSON type: {type(obj).__name__}"
        raise TypeError(msg)
    if (identity := id(obj)) in active:
        msg = "circular reference in identity JSON"
        raise ValueError(msg)
    active.add(identity)
    try:
        if isinstance(obj, list):
            return [_identity_snapshot(value, active) for value in obj]
        result: dict[str, Json] = {}
        for key, value in obj.items():
            if type(key) is not str:
                msg = "identity JSON requires exact string keys"
                raise TypeError(msg)
            key.encode()
            result[key] = _identity_snapshot(value, active)
        return result
    finally:
        active.remove(identity)


def canonical_strict(obj: object) -> bytes:
    """Encode an exact JSON value using the version-1 identity byte format.

    Accepts exact builtin dicts with exact string keys, lists, strings, integers,
    finite floats, booleans and None. Other types, subclasses, non-finite numbers,
    surrogate code points (including explicit pairs) and cycles are rejected
    without a user conversion hook.
    Shared acyclic branches are accepted. A private snapshot owns every mutable
    branch before serialization, so validation and encoding see the same values.

    Version 1 is sorted-key, compact, ASCII-escaped stdlib JSON with finite
    numbers. It matches :func:`canonical` on this restricted domain; it is not
    RFC 8785/JCS. Unicode normalization is not applied, and integer/float and
    signed-zero spellings remain distinct. Normalize application values before
    calling when those distinctions should not be part of an identity. Future
    incompatible formats require a separate API; existing canonical bytes stay
    unchanged. Interpreter integer-conversion and recursion limits still apply.
    """
    # Resolve the codec before copying, matching the original call's evaluation
    # order even when a property or allocation-triggered finalizer changes it.
    encoder = _IDENTITY.encode
    snapshot: Json | NotImplementedType = NotImplemented
    if _HAVE_ORJSON is True and _native_identity_snapshot is not None:
        snapshot = _native_identity_snapshot(obj)
    if snapshot is NotImplemented:
        snapshot = _identity_snapshot(obj, set())
    return encoder(snapshot).encode()


_IDENTITY = _stdlib.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
)

# `json.dumps` builds a fresh `JSONEncoder` whenever it is passed a non-default
# kwarg, which is every canonical call — a third of its cost on a small payload.
# The encoder is stateless between `encode` calls, so one serves every caller.
_CANONICAL = _stdlib.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
)


# Hooks and custom behavior bypass the native decoder entirely.
@wraps(_stdlib.loads)
def loads(s: str | bytes | bytearray, **kw: Any) -> Any:
    """Parse exact immutable input natively, retaining stdlib values and errors."""
    if _HAVE_MSGSPEC and not kw and (type(s) is str or type(s) is bytes):
        try:
            return _check_headroom(_decode(s))
        except (msgspec.DecodeError, UnicodeError, RecursionError):
            pass
    return _stdlib.loads(s, **kw)


_ARRAY_LOADS = loads


def dump(
    obj: object,
    fp: _TextWriter,
    *,
    skipkeys: bool = False,
    ensure_ascii: bool = True,
    check_circular: bool = True,
    allow_nan: bool = True,
    cls: type[JSONEncoder] | None = None,
    indent: int | str | None = None,
    separators: tuple[str, str] | None = None,
    default: Callable[[Any], Any] | None = None,
    sort_keys: bool = False,
    **kw: Any,
) -> None:
    """Stream JSON text to ``fp`` with stdlib's encoder and write behavior."""
    if cls is None and kw and kw.pop("numpy", False):
        default = _numpy_default(default)
    _stdlib.dump(
        obj,
        fp,
        skipkeys=skipkeys,
        ensure_ascii=ensure_ascii,
        check_circular=check_circular,
        allow_nan=allow_nan,
        cls=cls,
        indent=indent,
        separators=separators,
        default=default,
        sort_keys=sort_keys,
        **kw,
    )


@wraps(_stdlib.load)
def load(fp: _Reader, **kw: Any) -> Any:
    """Read once, then apply the same exact decoder dispatch as :func:`loads`."""
    return loads(fp.read(), **kw)


def dump_array(
    rows: Iterable[object],
    fp: io.TextIOBase | _BinaryWriter,
    *,
    chunk_size: int = 65536,
    sort_keys: bool = False,
    default: Callable[[object], object] | None = None,
    numpy: bool = False,
) -> int:
    """Write an iterable as a compact JSON array, returning its record count.

    Encode each record with :func:`dumpb` before advancing the iterable. Text
    streams (``io.TextIOBase``) receive Unicode; other sinks receive UTF-8
    bytes. Generators, binary/text gzip streams and short writes are supported.
    No optional dependency is required. Encoding options and recursion limits
    apply per record; the outer array is framing only.

    A positive integer ``chunk_size`` bounds each write request in binary bytes
    or text characters. Memory follows that bound plus the largest record,
    excluding values retained by the producer or encoder callbacks. Batching
    can consume multiple records before a write failure. The opening bracket
    is written before consuming records; the closing bracket follows successful
    consumption only. Errors propagate with partial output left in the sink;
    buffered records may not yet have been written. The caller owns the sink:
    it is neither flushed nor closed. Text streams retain their encoding policy.
    """
    from unstd.serde._array_writer import write_array

    size = index(chunk_size)
    if size < 1:
        message = "chunk_size must be positive"
        raise ValueError(message)
    return write_array(rows, fp, size, sort_keys, default, numpy)


def _stream_binary(fp: _StreamReader, size: int) -> bytes | bytearray:
    """Read a bounded binary chunk and reject a stream that changes type."""
    chunk = fp.read(size)
    if not isinstance(chunk, bytes | bytearray):
        message = "JSON binary stream read() must return bytes or bytearray"
        raise TypeError(message)
    return chunk


def _input_chunks(
    fp: _StreamReader, size: int, *, raw_utf8: bool = False
) -> Iterator[str | bytes]:
    """Decode short binary reads incrementally using stdlib's encoding rules."""
    chunk = fp.read(size)
    if isinstance(chunk, str):
        while chunk:
            yield chunk
            chunk = fp.read(size)
            if not isinstance(chunk, str):
                message = "JSON text stream read() must return str"
                raise TypeError(message)
        return
    if not isinstance(chunk, bytes | bytearray):
        message = "JSON stream read() must return str, bytes or bytearray"
        raise TypeError(message)

    # Four initial bytes distinguish BOMs and unmarked UTF-8/16/32. Even a
    # one-byte/short-read source must be sniffed before decoding its first byte.
    prefix = chunk if type(chunk) is bytes else bytes(memoryview(chunk))
    eof = not chunk
    while len(prefix) < 4 and not eof:
        part = _stream_binary(fp, min(size, 4 - len(prefix)))
        prefix += part
        eof = not part
    encoding = _stdlib.detect_encoding(prefix)
    if raw_utf8 and encoding in {"utf-8", "utf-8-sig"}:
        if encoding == "utf-8-sig":
            prefix = prefix[3:]
        if prefix:
            yield prefix
        while not eof:
            raw = _stream_binary(fp, size)
            eof = not raw
            if raw:
                # Read the buffer itself: bytes/bytearray subclasses may give
                # __bytes__ a meaning unrelated to the source's actual bytes.
                yield raw if type(raw) is bytes else bytes(memoryview(raw))
        return
    decoder = codecs.getincrementaldecoder(encoding)("surrogatepass")
    if text := decoder.decode(prefix, final=eof):
        yield text
    while not eof:
        chunk = _stream_binary(fp, size)
        eof = not chunk
        if text := decoder.decode(chunk, final=eof):
            yield text


def _stream_chunks(fp: _StreamReader, size: int) -> Iterator[str]:
    """Read decoded text for the portable per-item walker."""
    return cast("Iterator[str]", _input_chunks(fp, size))


def _decoded_chunks(chunks: Iterator[str | bytes]) -> Iterator[str]:
    """Decode an unfinished UTF-8 window and its suffix without rereading."""
    decoder = None
    for chunk in chunks:
        if isinstance(chunk, bytes):
            if decoder is None:
                decoder = codecs.getincrementaldecoder("utf-8")("surrogatepass")
            if text := decoder.decode(chunk):
                yield text
        else:
            yield chunk
    if decoder is not None and (text := decoder.decode(b"", final=True)):
        yield text


def _array_fallback(
    fp: _StreamReader,
    size: int,
    decode: Callable[[str], Any],
    text: str | bytes,
    chunks: Iterator[str | bytes],
    state: str,
) -> Iterator[Any]:
    """Resume the portable item walker without rereading a consumed window."""
    from itertools import chain

    from unstd.serde._stream import items

    remaining = _decoded_chunks(chain((text,), chunks))
    return items(fp, size, decode, chunks=remaining, state=state)


def _array_items(
    fp: _StreamReader, size: int, decode: Callable[[str], Any]
) -> Iterator[Any]:
    """Decode bounded native windows, loading the general walker only on refusal."""
    chunks = _input_chunks(fp, size, raw_utf8=True)
    pending: Any = ""
    carried = False
    state = "root"
    native = _native
    for chunk in chunks:
        text: Any = pending + chunk if pending else chunk
        pending = ""
        offset = 0
        if type(text) is not str and type(text) is not bytes:
            yield from _array_fallback(fp, size, decode, text, chunks, state)
            return
        binary = isinstance(text, bytes)
        # Syntax literals have the same byte/Unicode representation as text.
        whitespace: Any = b" \t\r\n" if binary else " \t\r\n"
        opening = 91 if binary else "["
        closing_array = 93 if binary else "]"
        comma = 44 if binary else ","
        closing_object: Any = b"}" if binary else "}"
        delimiters = b",]" if binary else ",]"
        while offset < len(text):
            offset = len(text) - len(text[offset:].lstrip(whitespace))
            if offset == len(text):
                break
            if state == "tail":
                yield from _array_fallback(
                    fp, size, decode, text[offset:], chunks, state
                )
                return
            if state == "root":
                if text[offset] != opening:
                    yield from _array_fallback(
                        fp, size, decode, text[offset:], chunks, state
                    )
                    return
                state = "first"
                offset += 1
                continue
            if text[offset] == closing_array:
                if state != "first":
                    yield from _array_fallback(
                        fp, size, decode, text[offset:], chunks, state
                    )
                    return
                state = "tail"
                offset += 1
                continue
            if text[offset] == comma:
                yield from _array_fallback(
                    fp, size, decode, text[offset:], chunks, state
                )
                return
            if (
                decode is not _ARRAY_LOADS
                or native is None
                or not native.dispatch_ready(decode)
            ):
                yield from _array_fallback(
                    fp, size, decode, text[offset:], chunks, state
                )
                return

            # The decoder itself can prove a complete object-record prefix.
            # An accepted synthetic array proves all its boundaries, including
            # quotes and nesting; a failed guess delegates to the C framer.
            end = -1
            values: Any = None
            closing = text.rfind(closing_object, offset)
            if closing >= offset:
                following = closing + 1
                while following < len(text) and text[following] in whitespace:
                    following += 1
                if following < len(text) and text[following] in delimiters:
                    try:
                        values = native.array_decode(text, offset, following, decode)
                    except (ValueError, RecursionError):
                        pass
                    else:
                        if values is NotImplemented:
                            yield from _array_fallback(
                                fp, size, decode, text[offset:], chunks, state
                            )
                            return
                        end = following
            if end < 0:
                span = native.array_boundary(text, offset)
                if span is NotImplemented:
                    yield from _array_fallback(
                        fp, size, decode, text[offset:], chunks, state
                    )
                    return
                end, _closed = span
                if end < 0:
                    # Try one short suffix in the next window. Large items and
                    # repeated short reads then use the linear fragment walker.
                    if not carried and len(text) - offset < size:
                        pending = text[offset:]
                        carried = True
                        break
                    yield from _array_fallback(
                        fp, size, decode, text[offset:], chunks, state
                    )
                    return
                try:
                    values = native.array_decode(text, offset, end, decode)
                except (ValueError, RecursionError):
                    # Replaying item boundaries preserves valid prefix delivery
                    # and item diagnostics. No custom hook has run in this path.
                    yield from _array_fallback(
                        fp, size, decode, text[offset:], chunks, state
                    )
                    return
                if values is NotImplemented:
                    yield from _array_fallback(
                        fp, size, decode, text[offset:], chunks, state
                    )
                    return
            state = "tail" if text[end] == closing_array else "value"
            offset = end + 1
            carried = False
            yield from values
            del values
    if state != "tail":
        yield from _array_fallback(fp, size, decode, pending, chunks, state)


def iterload(fp: _StreamReader, *, chunk_size: int = 65536, **kw: Any) -> Iterator[Any]:
    """Yield items from a top-level JSON array using bounded, incremental reads.

    Accepts text or binary streams, including compressed files. Binary streams
    use stdlib JSON's UTF-8/16/32 encoding detection. Default decoding can use
    bounded native batches with :func:`loads`; decoder options and hooks apply
    independently to each item. The outer array is framing only. No optional
    dependency is required.

    Reads start when iteration starts. Memory is proportional to ``chunk_size``
    plus the largest encoded/decoded item, excluding values retained by callers
    or hooks. The stream stays open, including on early termination. Exhaust the
    iterator to validate the closing bracket and trailing input: a later error
    can follow previously yielded values. JSONDecodeError positions refer to
    the current item or input chunk, not the complete document.

    ``chunk_size`` must be a positive integer and bounds each read request in
    bytes for binary streams or characters for text streams. Short reads work.
    """
    size = index(chunk_size)
    if size < 1:
        message = "chunk_size must be positive"
        raise ValueError(message)
    decode = (lambda text: loads(text, **kw)) if kw else loads
    if (
        not kw
        and decode is _ARRAY_LOADS
        and _native is not None
        and hasattr(_native, "array_decode")
        and _native.dispatch_ready(decode)
    ):
        return _array_items(fp, size, decode)
    from unstd.serde._stream import items

    return items(fp, size, decode)


if _native is not None:
    _native.configure(globals())
