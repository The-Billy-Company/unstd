"""Stdlib-compatible Base64 functions and optional accelerated convenience helpers.

``b64encode``/``b64decode``, ``standard_b64encode``/``standard_b64decode`` and
``urlsafe_b64encode``/``urlsafe_b64decode`` follow Python 3.12–3.14's Base64 API:
byte results, bytes-like inputs, arbitrary ``altchars``, lenient decoding by
default and optional ``validate=True``. MIME helpers ``encodebytes`` and
``decodebytes`` are stdlib's originals. The legacy stream functions are exposed
as ``encode_file``/``decode_file`` because ``encode``/``decode`` retain unstd's
existing convenience meanings. Other encodings (Base16/32/85) remain in stdlib.

``encode`` returns ASCII text, optionally URL-safe or unpadded. ``decode`` uses
strict standard decoding by default; ``decode(url=True)`` uses stdlib's lenient
URL alphabet with an extension accepting omitted padding. Ignored characters
do not count towards that padding. Strict validation does not check unused pad
bits for canonicality, just as in stdlib.

With the ``serde`` extra, pybase64 accelerates exact ``bytes`` encoding and
strict decoding of exact ``bytes`` or ASCII ``str``. The URL convenience decoder
also accelerates validated input after preserving its normalization rules.
Other input types and options use stdlib; native decode failures also return to
stdlib for its exact acceptance and diagnostics. This avoids pybase64 1.5's
Python 3.15-oriented lenient padding rules, Unicode coercion and
alternate-alphabet warnings.
Installing the extra must not change results, errors or warnings.

Contract sources: https://docs.python.org/3.12/library/base64.html,
https://github.com/python/cpython/blob/3.14/Lib/test/test_base64.py,
https://pybase64.readthedocs.io/en/stable/changelog.html, and RFC 4648 §3.5.
Differential and generated tests live under ``tests/suites/product/serde``.
"""

from __future__ import annotations

import base64 as _stdlib
from binascii import Error
from binascii import a2b_base64 as _a2b_base64
from binascii import b2a_base64 as _b2a_base64
from typing import TYPE_CHECKING

from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Buffer

    import pybase64 as _b64

    _HAVE_PYBASE64 = True
else:
    try:
        import pybase64 as _b64

        _HAVE_PYBASE64 = True
    except ImportError:  # base install without the `serde` extra — stdlib fallback
        import base64 as _b64

        _HAVE_PYBASE64 = False


encodebytes = _stdlib.encodebytes
decodebytes = _stdlib.decodebytes
encode_file = _stdlib.encode
decode_file = _stdlib.decode
urlsafe_b64decode = _stdlib.urlsafe_b64decode

__all__ = [
    "Error",
    "b64decode",
    "b64encode",
    "decode",
    "decode_file",
    "decode_json",
    "decode_text",
    "decodebytes",
    "encode",
    "encode_file",
    "encode_json",
    "encodebytes",
    "standard_b64decode",
    "standard_b64encode",
    "urlsafe_b64decode",
    "urlsafe_b64encode",
]

_URL_ENCODING = bytes.maketrans(b"+/", b"-_")
_URL_TRANSLATION = bytes.maketrans(b"-_", b"+/")
_URL_IGNORED = bytes(range(256)).translate(
    None, b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
_URL_INPUT_IGNORED = _URL_IGNORED.translate(None, b"-_")


def b64encode(s: Buffer, altchars: Buffer | None = None) -> bytes:
    """Stdlib ``b64encode``: bytes output with optional alternative alphabet."""
    if altchars is not None:
        # Stdlib owns alphabet validation, translation, buffer errors and their order.
        return _stdlib.b64encode(s, altchars)
    if _HAVE_PYBASE64 and type(s) is bytes:
        return _b64.b64encode(s)
    return _b2a_base64(s, newline=False)


def b64decode(
    s: str | Buffer, altchars: str | Buffer | None = None, validate: bool = False
) -> bytes:
    """Stdlib ``b64decode``, including lenient defaults and exact diagnostics."""
    if altchars is None and (type(s) is bytes or (type(s) is str and s.isascii())):
        # Native dispatch costs more than binascii on tiny documents. Exact ASCII
        # strings suit both codecs; subclasses keep stdlib's coercion hooks below.
        if _HAVE_PYBASE64 and validate is True and len(s) >= 256:
            try:
                return _b64.b64decode(s, None, True)
            except Error:
                # Backend errors are less specific, and supported Python patch
                # releases can differ on excess padding. Stdlib is authoritative.
                pass
        return _a2b_base64(s, strict_mode=validate)
    return _stdlib.b64decode(s, altchars, validate)


def standard_b64encode(s: Buffer) -> bytes:
    """Stdlib standard-alphabet encoding, returning bytes."""
    if _HAVE_PYBASE64 and type(s) is bytes:
        return _b64.b64encode(s)
    return _b2a_base64(s, newline=False)


def standard_b64decode(s: str | Buffer) -> bytes:
    """Stdlib standard-alphabet decoding, discarding non-alphabet bytes."""
    if type(s) is bytes or (type(s) is str and s.isascii()):
        return _a2b_base64(s)
    return _stdlib.b64decode(s)


def urlsafe_b64encode(s: Buffer) -> bytes:
    """Stdlib URL-safe encoding, retaining padding and returning bytes."""
    if _HAVE_PYBASE64 and type(s) is bytes:
        return _b64.b64encode(s, b"-_")
    return _b2a_base64(s, newline=False).translate(_URL_ENCODING)


def encode(data: Buffer, *, url: bool = False, pad: bool = True) -> str:
    """Base64-encode *data* to an ASCII ``str``.

    ``url=True`` selects the ``-``/``_`` alphabet (URLs, filenames, MIME, tokens);
    ``pad=False`` drops the trailing ``=``.
    """
    if _HAVE_PYBASE64 and type(data) is bytes:
        # The SIMD encoder writes `str` directly; a `bytes` round trip is ~1.8x slower.
        out = _b64.b64encode_as_string(data, b"-_" if url else None)
    else:
        raw = _b2a_base64(data, newline=False)
        out = (raw.translate(_URL_ENCODING) if url else raw).decode("ascii")
    return out if pad else out.rstrip("=")


def _decode_input(s: str | Buffer) -> bytes | bytearray:
    """Normalize URL-helper inputs with stdlib's modern decode coercion rules."""
    if isinstance(s, str):
        try:
            return s.encode("ascii")
        except UnicodeEncodeError:
            msg = "string argument should contain only ASCII characters"
            raise ValueError(msg) from None
    if isinstance(s, (bytes, bytearray)):
        return s
    try:
        return memoryview(s).tobytes()
    except TypeError:
        msg = (
            "argument should be a bytes-like object or ASCII "
            f"string, not {s.__class__.__name__!r}"
        )
        raise TypeError(msg) from None


def decode(s: str | Buffer, *, url: bool = False) -> bytes:
    """Base64-decode *s* to ``bytes``.

    Standard (default): strict — non-alphabet characters or missing padding raise
    :data:`Error`, so malformed wire payloads fail loudly. ``url=True``: the
    lenient url-safe alphabet, padded or not.
    """
    if not url:
        return b64decode(s, validate=True)
    native_rejected = False
    raw: bytes | bytearray
    if type(s) is bytes:
        raw = s
    elif type(s) is str and s.isascii():
        # A clean exact string goes straight to the native codec, skipping a
        # Python-level ASCII copy; a rejection is not repeated on the bytes path.
        if _HAVE_PYBASE64 and len(s) >= 128 and "+" not in s and "/" not in s:
            try:
                return _b64.b64decode(s, b"-_", True, padded=s.endswith("="))
            except Error:
                native_rejected = True
        raw = s.encode("ascii")
    else:
        raw = _decode_input(s)
    if type(raw) is bytes:
        size = len(raw)
        # For tiny values, native dispatch costs more than normalization.
        big = _HAVE_PYBASE64 and size >= 128
        native = big and not native_rejected
        if native and b"+" not in raw and b"/" not in raw:
            # Strict native decoding proves a clean URL value. Unpadded mode only
            # applies without a terminal '='; odd padding takes the stdlib path.
            try:
                return _b64.b64decode(raw, b"-_", True, padded=raw.endswith(b"="))
            except Error:
                native = False
        # Exact bytes have no translation hooks: translate and drop ignored
        # characters in one pass, keeping both alphabets until translation.
        raw = raw.translate(_URL_TRANSLATION, _URL_INPUT_IGNORED)
        # Retry a rejected native value only if filtering changed its content;
        # padding-only failures already have a cheap authoritative stdlib path.
        if len(raw) < size:
            native = big
    else:
        # A subclass's translation and its result's normalization are observable.
        # Preserve both before filtering, without invoking that subclass twice.
        raw = _decode_input(raw.translate(_URL_TRANSLATION))
        if type(raw) is not bytes:
            raw = memoryview(raw).tobytes()
        raw = raw.translate(None, _URL_IGNORED)
        native = _HAVE_PYBASE64 and len(raw) >= 128
    raw += b"=" * (-len(raw) % 4)
    if native:
        try:
            return _b64.b64decode(raw, None, True)
        except Error:
            # Internal '=' and trailing data retain stdlib's lenient meaning.
            pass
    return _a2b_base64(raw)


def decode_text(s: str | Buffer, *, url: bool = False, errors: str = "replace") -> str:
    """:func:`decode` *s*, then UTF-8-decode the bytes to text."""
    return decode(s, url=url).decode("utf-8", errors)


def encode_json(obj: object) -> str:
    """Base64 ``str`` of *obj*'s compact JSON — smuggling a blob through one CLI argument or env slot."""
    return encode(jsonx.dumpb(obj))


def decode_json(s: str | Buffer) -> jsonx.Json:
    """Inverse of :func:`encode_json` — :func:`decode` *s*, then parse the JSON."""
    value: jsonx.Json = jsonx.loads(decode(s))
    return value
