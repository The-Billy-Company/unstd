"""Exact stdlib JSON semantics with guarded native acceleration.

``dumps`` uses orjson only for bounded exact builtin trees whose output matches
stdlib byte-for-byte. The optional native guard proves eligibility and encodes
without releasing the GIL or invoking Python callbacks. Its portable fallback
uses private snapshots. Floats, unsupported types, large integers, complex
formatting and user callbacks take stdlib paths.
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

from functools import wraps
import json as _stdlib
from typing import TYPE_CHECKING, Any

from unstd.serde._json_common import _check_headroom
from unstd.serde._json_encode import (
    _compact_bytes,
    _encoder,
    _exact_bytes,
    _exact_encode,
    _format,
    _native,
)


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import NotImplementedType
    from typing import Protocol

    import msgspec
    import numpy as np
    import orjson

    _HAVE_ORJSON = True
    _HAVE_MSGSPEC = True
    _HAVE_NUMPY = True

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

else:
    try:
        import orjson

        _HAVE_ORJSON = True
    except ImportError:  # base install without the `serde` extra — stdlib fallback
        orjson = None
        _HAVE_ORJSON = False
    try:
        import msgspec

        _HAVE_MSGSPEC = True
    except ImportError:
        msgspec = None
        _HAVE_MSGSPEC = False
    try:
        import numpy as np

        _HAVE_NUMPY = True
    except ImportError:  # numpy is not part of the `serde` extra — opt-in only
        np = None
        _HAVE_NUMPY = False


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
else:
    _SORT = _INDENT = _NEWLINE = 0
if _HAVE_MSGSPEC:
    # The native parser constructs exact integers and correctly rounded floats.
    _decode = msgspec.json.Decoder().decode
_NO_NUMPY = "numpy=True requires numpy to be installed"
_DEFAULT_ENCODER = _stdlib.JSONEncoder()
_native_dumpb: (
    Callable[[object, bool, object, bool], bytes | NotImplementedType] | None
) = _native.dumpb if _native is not None else None
_native_line: Callable[[object], bytes | NotImplementedType] | None = (
    _native.line if _native is not None else None
)


def _numpy_default(
    user_default: Callable[[object], object] | None,
) -> Callable[[object], object]:
    """Wrap *user_default* so numpy values degrade to JSON-native ones first.

    An ndarray/numpy scalar becomes a JSON-native value before falling through
    to whatever the caller already supplied.

    The extension uses the same conversion regardless of installed backends.
    """

    def _default(o: object) -> object:
        if _HAVE_NUMPY:
            if isinstance(o, np.ndarray):
                return o.tolist()
            if isinstance(o, np.generic):
                return o.item()
        if user_default is not None:
            return user_default(o)
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
        if not _HAVE_NUMPY:
            raise TypeError(_NO_NUMPY)
        default = _numpy_default(default)
    encoder = None
    style = -1
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
            style = 1
            encoder = _DEFAULT_ENCODER
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
        try:
            result = _exact_encode(
                obj,
                ensure_ascii,
                style,
                (_SORT if sort_keys else 0) | (_INDENT if style == 2 else 0),
                _encode,
            )
            if result is not None:
                return result
        except (orjson.JSONEncodeError, RecursionError, RuntimeError):
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

    This extension uses ``ensure_ascii=False`` and compact separators. orjson
    handles proven exact builtin trees; other values, ``default`` and ``numpy=True``
    use stdlib. For the package's identity format use :func:`canonical`.
    """
    if _native_dumpb is not None:
        native_result = _native_dumpb(obj, sort_keys, default, numpy)
        if native_result is not NotImplemented:
            return native_result
    if numpy and not _HAVE_NUMPY:
        raise TypeError(_NO_NUMPY)
    if _HAVE_ORJSON and not numpy and default is None and type(sort_keys) is bool:
        try:
            result = _exact_bytes(obj, _SORT if sort_keys else 0, _encode)
            if result is not None:
                return result
        except (orjson.JSONEncodeError, RecursionError, RuntimeError):
            # Lone surrogates and a low Python recursion limit take stdlib's
            # path. No user callback has run during this eligibility probe.
            pass
    if (
        type(numpy) is bool
        and not numpy
        and default is None
        and type(sort_keys) is bool
    ):
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
    if _native_line is not None:
        native_result = _native_line(obj)
        if native_result is not NotImplemented:
            return native_result
    if _HAVE_ORJSON:
        try:
            result = _exact_bytes(obj, _NEWLINE, _encode)
            if result is not None:
                return result
        except (orjson.JSONEncodeError, RecursionError, RuntimeError):
            pass
    return _compact_bytes(obj, False) + b"\n"


def canonical(
    obj: object, *, default: Callable[[object], object] | None = str
) -> bytes:
    """Stable canonical JSON *bytes* for identity — digests, HMACs, cache keys, fingerprints.

    Sorted keys, compact separators, ASCII-escaped. Deterministic and independent
    of the active codec (stdlib vs orjson) *and* of any Unicode-handling change,
    which is exactly what a hash/identity input needs. Deliberately stdlib-backed
    (identity is never the hot path): the ASCII escaping makes the bytes
    reproducible across versions, and this form is byte-identical to the historic
    ``json.dumps(obj, sort_keys=True, separators=(",", ":"))`` output — so swapping
    a hash site onto it changes nothing on the wire.

    Use this whenever serialized bytes feed something *compared or persisted across
    time*; use :func:`dumps`/:func:`dumpb` for machine transport where only the
    parsed value matters.
    """
    if default is str:
        return _CANONICAL.encode(obj).encode()
    return _stdlib.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=default
    ).encode()


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
        if not _HAVE_NUMPY:
            raise TypeError(_NO_NUMPY)
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
