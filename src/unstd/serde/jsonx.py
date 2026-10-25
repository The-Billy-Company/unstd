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

from collections.abc import Callable  # noqa: TC003 — public annotation introspection
from functools import wraps
import json as _stdlib
from math import isfinite
from typing import TYPE_CHECKING, Any, Protocol

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
    "dumpb",
    "dumps",
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


if _native is not None:
    _native.configure(globals())
