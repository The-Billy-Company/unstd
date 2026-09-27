"""Drop-in accelerated Base64 — pybase64 under a stdlib-``base64``-faithful surface.

The stdlib ``base64`` module is C-implemented but scalar; ``pybase64`` wraps
`libbase64 <https://github.com/aklomp/base64>`_, which dispatches to an
AVX2/NEON SIMD codec at runtime (multi-GB/s encode) with a pure-Python
fallback. Callers that base64 genuinely large payloads — PCM audio, PNG
thumbnails / screenshots, f16 embedding vectors — justify the SIMD codec over
the scalar stdlib path.

One verb per direction, with the alphabet and padding as keywords rather than
as more function names::

    from unstd.serde import b64

    s = b64.encode(data)  # bytes -> str (standard, padded)
    s = b64.encode(data, url=True, pad=False)  # bytes -> str (base64url, JWT-style)
    data = b64.decode(s)  # str|bytes -> bytes (standard, validated)
    data = b64.decode(s, url=True)  # str|bytes -> bytes (url-safe, padding optional)
    text = b64.decode_text(s)  # str|bytes -> str (utf-8, errors="replace")
    blob = b64.encode_json(obj)  # any JSON value -> str
    obj = b64.decode_json(blob)  # str|bytes -> Json

Backend & fallback: pybase64 is provided by the ``serde`` extra. When it is
absent (a base ``unstd`` install) every call transparently uses the stdlib
``base64`` module — same surface, byte-identical output, scalar speed — so the
module works everywhere.

Faithfulness contract — verified byte-for-byte against stdlib ``base64`` in
``tests/serde/test_b64.py``:

- **Encode** produces exactly the bytes stdlib emits, handed back as ``str`` (the
  ubiquitous ``base64.b64encode(x).decode("ascii")`` idiom fused into one call).
  ``pad=False`` drops the trailing ``=`` — the unpadded base64url that JWTs,
  WebAuthn, and bearer tokens put on the wire.
- **Standard decode** passes ``validate=True`` so non-alphabet bytes raise
  :data:`Error` instead of being silently discarded — a wire payload that isn't
  clean base64 is a bug, not something to truncate past — and requires canonical
  padding.
- **Url-safe decode** is stdlib ``urlsafe_b64decode``'s lenient alphabet, and
  accepts padded *and* unpadded input. The alphabet exists for URLs, headers,
  and tokens, where the ``=`` is routinely dropped, so demanding it back would
  only move the ``rstrip``/re-pad dance to every call site.
- Every decoder accepts ``str`` *or* ``bytes``, so callers stop sprinkling
  ``str(...)`` / ``.encode()`` coercions at the boundary.

``binascii.Error`` is re-exported so ``except b64.Error`` keeps catching the
same failure stdlib raised.
"""

from __future__ import annotations

from binascii import Error
from typing import TYPE_CHECKING

from unstd.serde import jsonx


if TYPE_CHECKING:
    # Every call below sticks to the API surface the two backends share (plus the
    # `_HAVE_PYBASE64`-guarded calls), so type-checking against pybase64 alone is
    # faithful for both.
    import pybase64 as _b64

    _HAVE_PYBASE64 = True
else:
    try:
        import pybase64 as _b64

        _HAVE_PYBASE64 = True
    except ImportError:  # base install without the `serde` extra — stdlib fallback
        import base64 as _b64

        _HAVE_PYBASE64 = False


__all__ = ["Error", "decode", "decode_json", "decode_text", "encode", "encode_json"]


def encode(data: bytes, *, url: bool = False, pad: bool = True) -> str:
    """Base64-encode *data* to an ASCII ``str``.

    ``url=True`` selects the ``-``/``_`` alphabet (URLs, filenames, MIME, tokens);
    ``pad=False`` drops the trailing ``=``.
    """
    if _HAVE_PYBASE64:
        # `altchars` rides the same SIMD encoder straight to `str`, where
        # `urlsafe_b64encode` round-trips through `bytes` first — ~1.8x slower.
        out = (
            _b64.b64encode_as_string(data, altchars=b"-_")
            if url
            else _b64.b64encode_as_string(data)
        )
    else:
        out = (_b64.urlsafe_b64encode(data) if url else _b64.b64encode(data)).decode(
            "ascii"
        )
    return out if pad else out.rstrip("=")


def decode(s: str | bytes, *, url: bool = False) -> bytes:
    """Base64-decode *s* to ``bytes``.

    Standard (default): strict — non-alphabet characters or missing padding raise
    :data:`Error`, so malformed wire payloads fail loudly. ``url=True``: the
    lenient url-safe alphabet, padded or not.
    """
    if not url:
        return _b64.b64decode(s, validate=True)
    if _HAVE_PYBASE64:
        return _b64.urlsafe_b64decode(s, padded=False)
    raw = s.encode("ascii") if isinstance(s, str) else s
    return _b64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def decode_text(s: str | bytes, *, url: bool = False, errors: str = "replace") -> str:
    """:func:`decode` *s*, then UTF-8-decode the bytes to text."""
    return decode(s, url=url).decode("utf-8", errors)


def encode_json(obj: object) -> str:
    """Base64 ``str`` of *obj*'s compact JSON — smuggling a blob through one CLI argument or env slot."""
    return encode(jsonx.dumpb(obj))


def decode_json(s: str | bytes) -> jsonx.Json:
    """Inverse of :func:`encode_json` — :func:`decode` *s*, then parse the JSON."""
    return jsonx.loads(decode(s))
