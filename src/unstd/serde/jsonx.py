r"""Drop-in accelerated JSON — orjson under a stdlib-``json``-faithful surface.

The stdlib ``json`` module is pure-Python on the hot encode/decode path; orjson
is a C/Rust codec several times faster. This shim wraps orjson but presents the
*exact* stdlib call surface (``str`` in/out, ``dumps``/``loads``/``dump``/``load``,
``JSONDecodeError``, ``JSONDecoder``/``JSONEncoder``) so a caller migrates by
swapping the import and the module name:

    from unstd.serde import jsonx     # then jsonx.dumps(...) / jsonx.loads(...)

Backend & fallback: orjson is provided by the ``serde`` extra. When it
is installed the fast path is taken and output is byte-identical to the historic
shim. When it is **not** (a base ``unstd`` install), every function transparently
falls back to the stdlib ``json`` module — same surface, correct JSON, just
slower — so the module imports and works everywhere. Production consumers that
opt into ``unstd[serde]`` never hit the fallback.

Faithfulness contract — what stays identical to stdlib ``json``:

- ``dumps`` returns ``str`` (orjson returns ``bytes`` — we decode once).
- Non-string dict keys are coerced to strings (``OPT_NON_STR_KEYS``), matching
  the stdlib behavior of stringifying ``int``/``float``/``bool``/``None`` keys.
- ``datetime``/``date``/``time`` are *passed through* to ``default``
  (``OPT_PASSTHROUGH_DATETIME``) rather than emitted as orjson's native RFC-3339,
  so ``dumps(dt, default=str)`` yields the same ``str(dt)`` the stdlib produced,
  and ``dumps(dt)`` with no ``default`` raises ``TypeError`` exactly as stdlib.
- ``default`` is honored identically (called for unknown types, result re-encoded).
- ``sort_keys=True`` maps to ``OPT_SORT_KEYS``; the compact
  ``separators=(",", ":")`` idiom is orjson's native output (no-op fast path).
- ``JSONDecodeError`` is re-exported from stdlib; orjson raises a *subclass* of
  it, so existing ``except json.JSONDecodeError`` clauses keep catching failures.
- ``JSONDecoder``/``JSONEncoder`` are re-exported unchanged for the rare
  streaming ``raw_decode`` / custom-encoder callers (orjson has no equivalent).

Format difference (recovered by fallback): orjson output is always UTF-8 with
non-ASCII left unescaped and no inter-token whitespace. That is valid JSON that
parses identically; it differs byte-for-byte from stdlib's ``\\uXXXX`` escaping
and ``", "``/``": "`` spacing. Any call that needs stdlib's *exact bytes* —
``ensure_ascii=True``, an ``indent`` other than 2, arbitrary ``separators``, or
any other stdlib-only kwarg (``cls``, ``skipkeys``, …) — transparently falls
back to stdlib ``json`` so that output stays identical.

Value differences (NOT recovered — orjson is a fixed-width, finite codec, so no
format kwarg triggers a fallback here). These are deliberate and pinned as
regression guards in ``tests/properties/test_serde_parity.py`` and
``tests/serde/test_jsonx.py``:

- **Non-finite floats** — ``NaN`` / ``Infinity`` / ``-Infinity`` serialize to
  JSON ``null`` (silently — ``default`` does *not* fire), where stdlib emits the
  technically-invalid ``NaN`` / ``Infinity`` literals. A struct that must
  surface a non-finite value should use a typed ``serde.structs`` codec (which
  raises) rather than rely on ``jsonx``.
- **Integers beyond 64-bit** — on encode, an int outside signed/unsigned 64-bit
  range raises ``TypeError`` (stdlib serializes arbitrary precision); on decode,
  ``loads`` coerces an out-of-range integer literal to ``float`` (lossy, silent —
  orjson has no arbitrary-precision path), where stdlib returns the exact ``int``.
  Stringify large IDs before encoding if they can exceed 2⁶⁴.
- **Deep nesting** — nesting past orjson's recursion cap (~254) raises
  ``TypeError`` (stdlib recurses to the interpreter limit).
- **Small/large float notation** — orjson prefers fixed notation (``0.00001``)
  where stdlib switches to exponent form (``1e-05``). Same value, different
  bytes — parses back identically, so it never corrupts data, but it is not
  byte-parity with stdlib.

Use this shim on machine-to-machine JSON (wire payloads, Redis envelopes, cache
keys, tool results), not on human-facing pretty-print.
"""

from __future__ import annotations

import json as _stdlib
from typing import IO, TYPE_CHECKING, TypedDict, Unpack


if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    import orjson

    _HAVE_ORJSON = True
    _HAVE_NUMPY = True
else:
    try:
        import orjson

        _HAVE_ORJSON = True
    except ImportError:  # base install without the `serde` extra — stdlib fallback
        orjson = None
        _HAVE_ORJSON = False
    try:
        import numpy as np

        _HAVE_NUMPY = True
    except ImportError:  # numpy is not part of the `serde` extra — opt-in only
        np = None
        _HAVE_NUMPY = False


JSONDecodeError = _stdlib.JSONDecodeError
JSONDecoder = _stdlib.JSONDecoder
JSONEncoder = _stdlib.JSONEncoder

# The recursive shape of any parsed JSON value — what `loads`/`load` hand back.
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
    _decode = orjson.loads
    _BASE = orjson.OPT_NON_STR_KEYS | orjson.OPT_PASSTHROUGH_DATETIME
    _SORT, _INDENT, _NUMPY = (
        orjson.OPT_SORT_KEYS,
        orjson.OPT_INDENT_2,
        orjson.OPT_SERIALIZE_NUMPY,
    )
    _LINE = orjson.OPT_APPEND_NEWLINE
else:
    _BASE = _SORT = _INDENT = _NUMPY = _LINE = 0
# Both tuple/list spellings of the minified idiom — orjson's native output.
_COMPACT = ((",", ":"), [",", ":"])
_NO_NUMPY = "numpy=True requires numpy to be installed"


def _numpy_default(
    user_default: Callable[[object], object] | None,
) -> Callable[[object], object]:
    """Wrap *user_default* so numpy values degrade to JSON-native ones first.

    An ndarray/numpy scalar becomes a JSON-native value before falling through
    to whatever the caller already supplied.

    Only reached on the stdlib path — orjson's own ``OPT_SERIALIZE_NUMPY`` handles
    the fast path natively and never calls ``default`` for a numpy value.
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


class _StdlibDumpsKw(TypedDict, total=False):
    """stdlib-only ``json.dumps`` kwargs — any of these forces the stdlib fallback."""

    skipkeys: bool
    check_circular: bool
    allow_nan: bool
    cls: type[_stdlib.JSONEncoder] | None


class _StdlibLoadsKw(TypedDict, total=False):
    """stdlib-only ``json.loads`` hook kwargs — any of these forces the stdlib fallback."""

    cls: type[_stdlib.JSONDecoder] | None
    object_hook: Callable[[dict[str, object]], object] | None
    parse_float: Callable[[str], object] | None
    parse_int: Callable[[str], object] | None
    parse_constant: Callable[[str], object] | None
    object_pairs_hook: Callable[[list[tuple[str, object]]], object] | None


def dumps(
    obj: object,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    default: Callable[[object], object] | None = None,
    ensure_ascii: bool = False,
    separators: tuple[str, str] | None = None,
    numpy: bool = False,
    **kw: Unpack[_StdlibDumpsKw],
) -> str:
    """Serialize ``obj`` to a JSON ``str`` (orjson fast path, stdlib for exact-format cases).

    ``numpy=True`` lets an ``ndarray``/numpy scalar serialize directly instead of
    raising — the orjson path takes its native ``OPT_SERIALIZE_NUMPY`` (C/int/
    float/bool dtypes, native byte order, no ``object`` arrays); the stdlib
    fallback path degrades the same values via ``default`` (``.tolist()``/
    ``.item()``) first, then defers to any *user*-supplied ``default``. Requires
    numpy (not part of the ``serde`` extra — install it separately, or pull in
    ``unstd[pack]``/``unstd[rand]``/``unstd[audio]``, which already depend on it).
    """
    if numpy and not _HAVE_NUMPY:
        raise TypeError(_NO_NUMPY)
    # Ordered cheapest-first: the overwhelmingly common call passes nothing, and
    # every test below short-circuits on a falsy default.
    if (
        kw
        or ensure_ascii
        or (indent and indent != 2)
        or (separators is not None and separators not in _COMPACT)
        or not _HAVE_ORJSON
    ):
        # Base install (no orjson): emit compact, non-ASCII-preserving output to
        # approximate the orjson surface when the caller passed no format kwargs.
        if not _HAVE_ORJSON and separators is None and indent is None:
            separators = (",", ":")
        return _stdlib.dumps(
            obj,
            indent=indent,
            sort_keys=sort_keys,
            default=_numpy_default(default) if numpy else default,
            ensure_ascii=ensure_ascii,
            separators=separators,
            **kw,
        )
    opt = _BASE
    if sort_keys:
        opt |= _SORT
    if indent:
        opt |= _INDENT
    if numpy:
        opt |= _NUMPY
    return _encode(obj, default, opt).decode()


def dumpb(
    obj: object,
    *,
    sort_keys: bool = False,
    default: Callable[[object], object] | None = None,
    numpy: bool = False,
) -> bytes:
    """Serialize straight to ``bytes`` — skips the ``str`` decode for callers that immediately write to a socket / Redis / hash (the ``dumps(...).encode()`` idiom).

    See :func:`dumps` for what ``numpy=True`` does on each backend.
    """
    if not _HAVE_ORJSON:
        return dumps(obj, sort_keys=sort_keys, default=default, numpy=numpy).encode()
    if numpy and not _HAVE_NUMPY:
        raise TypeError(_NO_NUMPY)
    opt = _BASE
    if sort_keys:
        opt |= _SORT
    if numpy:
        opt |= _NUMPY
    return _encode(obj, default, opt)


def _line(obj: object) -> bytes:
    """One newline-terminated compact record — :mod:`unstd.serde.ndjson`'s encoder.

    orjson appends the newline itself (``OPT_APPEND_NEWLINE``), so a row never
    pays a second ``bytes`` allocation for the concatenation.
    """
    if _HAVE_ORJSON:
        return _encode(obj, None, _LINE | _BASE)
    return dumps(obj).encode() + b"\n"


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


def loads(s: str | bytes | bytearray, **kw: Unpack[_StdlibLoadsKw]) -> Json:
    """Parse a JSON ``str``/``bytes``. Falls back to stdlib for hook kwargs (``object_hook``, ``parse_float``, …) that orjson does not support."""
    # Both decoders are typed `Any` at their boundary — a JSON document's shape
    # is only known at runtime. Annotating the local names `Json` as the contract
    # at zero runtime cost, where `typing.cast` is a real call on the hottest path.
    value: Json = _stdlib.loads(s, **kw) if kw or not _HAVE_ORJSON else _decode(s)
    return value


def dump(
    obj: object,
    fp: IO[str],
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    default: Callable[[object], object] | None = None,
    ensure_ascii: bool = False,
    separators: tuple[str, str] | None = None,
    numpy: bool = False,
    **kw: Unpack[_StdlibDumpsKw],
) -> None:
    """Write ``obj`` as JSON to text file object ``fp`` (stdlib ``json.dump`` twin)."""
    fp.write(
        dumps(
            obj,
            indent=indent,
            sort_keys=sort_keys,
            default=default,
            ensure_ascii=ensure_ascii,
            separators=separators,
            numpy=numpy,
            **kw,
        )
    )


def load(fp: IO[str] | IO[bytes], **kw: Unpack[_StdlibLoadsKw]) -> Json:
    """Read and parse JSON from file object ``fp`` (stdlib ``json.load`` twin)."""
    return loads(fp.read(), **kw)
