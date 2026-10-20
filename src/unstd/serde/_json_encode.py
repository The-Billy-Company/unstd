"""Exact-output JSON proofs and reusable stdlib encoders.

The optional native guard validates exact builtins and calls its trusted orjson
backend while holding the GIL, without running Python code between them. The
portable path uses a small private snapshot instead. Failed proofs use stdlib.
"""

from __future__ import annotations

from functools import lru_cache
import json
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType, NotImplementedType


_native_encode: Callable[..., bytes | NotImplementedType] | None
_native_backend: Callable[..., bytes] | None
_native: ModuleType | None
try:
    from orjson import dumps as _native_backend

    from unstd.serde import _json_native as _native_module

    _configure_native = _native_module.configure
    _native_encode = _native_module.encode
    _native = _native_module
except (ImportError, AttributeError):
    _native = None
    _native_encode = None
    _native_backend = None


_UNSAFE = object()
_COMPACT = (",", ":")
_SPACED = (", ", ": ")
_MAX_VALUES = 16


def _format(indent: Any, separators: Any) -> int:
    """A common builtin configuration, without evaluating user-defined methods.

    Zero means compact, one means stdlib default spacing, two means indent=2;
    three is another cacheable format and minus one requires direct stdlib.
    """
    if indent is not None and not (
        (type(indent) is int and indent in (0, 2, 4))
        or (type(indent) is str and indent == "\t")
    ):
        return -1
    if separators is not None and not (
        type(separators) is tuple
        and len(separators) == 2
        and type(separators[0]) is str
        and type(separators[1]) is str
        and separators in (_COMPACT, _SPACED)
    ):
        return -1
    if indent is None:
        return 0 if separators == _COMPACT else 1
    if type(indent) is int and indent == 2 and separators is None:
        return 2
    return 3


def _snapshot(obj: Any, remaining: list[int], spaced: bool, depth: int = 0) -> Any:
    """Copy only an exact builtin tree whose backend output is provable."""
    remaining[0] -= 1
    if remaining[0] < 0:
        return _UNSAFE
    kind = type(obj)
    if kind is str:
        return _UNSAFE if spaced and ("," in obj or ":" in obj) else obj
    if obj is None or kind is bool:
        return obj
    if kind is int:
        return obj if -(2**63) <= obj < 2**64 else _UNSAFE
    if depth >= 16:
        return _UNSAFE
    if kind is list or kind is tuple:
        size = len(obj)
        if size > remaining[0]:
            return _UNSAFE
        if size >= 8:
            values = list(obj)
            size = len(values)
            if size > remaining[0]:
                return _UNSAFE
            # Class hashing/equality can be user-defined through a metaclass.
            # Identity checks must precede every numeric operation.
            for value in values:
                if type(value) is not int:
                    break
            else:
                if min(values) < -(2**63) or max(values) >= 2**64:
                    return _UNSAFE
                # Keep the recursive scalar frame: exhausting Python's stack
                # must still reject a tree which stdlib cannot serialize.
                if _snapshot(values[0], remaining, spaced, depth + 1) is _UNSAFE:
                    return _UNSAFE
                remaining[0] -= size - 1
                return values
        result = []
        for item in obj:
            value = _snapshot(item, remaining, spaced, depth + 1)
            if value is _UNSAFE:
                return _UNSAFE
            result.append(value)
        return result
    if kind is dict:
        if len(obj) > remaining[0]:
            return _UNSAFE
        pairs = {}
        for key, item in obj.items():
            if type(key) is not str or (spaced and ("," in key or ":" in key)):
                return _UNSAFE
            value = _snapshot(item, remaining, spaced, depth + 1)
            if value is _UNSAFE:
                return _UNSAFE
            pairs[key] = value
        return pairs
    return _UNSAFE


def _exact_encode(
    obj: object,
    ensure_ascii: bool,
    style: int,
    option: int,
    encode: Callable[..., bytes],
) -> str | None:
    """Serialize only after a bounded proof; the caller owns error fallback."""
    native_spacing = False
    scalar_string = type(obj) is str
    # Python 3.12's indented encoder recurses in Python for each container.
    # The recursive snapshot retains that headroom across supported versions.
    if _native_encode is not None and encode is _native_backend and style != 2:
        result = _native_encode(obj, option, style == 1)
        if result is NotImplemented:
            return None
        raw = result
        native_spacing = style == 1
    else:
        snapshot = obj if scalar_string else _snapshot(obj, [_MAX_VALUES], style == 1)
        if snapshot is _UNSAFE:
            return None
        raw = encode(snapshot, option=option)
    # DEL is ASCII but stdlib's ASCII encoder escapes it. All other ASCII
    # escapes match; non-ASCII and surrogate cases belong to stdlib.
    if ensure_ascii and (not raw.isascii() or b"\x7f" in raw):
        return None
    if style == 1 and not scalar_string and not native_spacing:
        raw = raw.replace(b",", b", ").replace(b":", b": ")
    return raw.decode()


def _exact_bytes(
    obj: object, option: int, encode: Callable[..., bytes]
) -> bytes | None:
    """The same exact snapshot domain, in compact UTF-8 without a text roundtrip."""
    if _native_encode is not None and encode is _native_backend:
        result = _native_encode(obj, option)
        return None if result is NotImplemented else result
    snapshot = _snapshot(obj, [_MAX_VALUES], False)
    return None if snapshot is _UNSAFE else encode(snapshot, option=option)


@lru_cache(maxsize=32)
def _encoder(
    skipkeys: bool,
    ensure_ascii: bool,
    check_circular: bool,
    allow_nan: bool,
    sort_keys: bool,
    indent: int | str | None,
    separators: tuple[str, str] | None,
) -> json.JSONEncoder:
    """A bounded cache of stateless stdlib encoders for builtin options only."""
    return json.JSONEncoder(
        skipkeys=skipkeys,
        ensure_ascii=ensure_ascii,
        check_circular=check_circular,
        allow_nan=allow_nan,
        sort_keys=sort_keys,
        indent=indent,
        separators=separators,
    )


def _compact_bytes(obj: object, sort_keys: bool) -> bytes:
    """A compact stdlib fallback retaining the public dumps wrapper's frame."""
    return (
        _encoder(False, False, True, True, sort_keys, None, _COMPACT)
        .encode(obj)
        .encode()
    )
