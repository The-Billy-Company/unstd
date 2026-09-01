"""Drop-in accelerated Base64 — pybase64 under a stdlib-``base64``-faithful surface.

The stdlib ``base64`` module is C-implemented but scalar; ``pybase64`` wraps
`libbase64 <https://github.com/aklomp/base64>`_, which dispatches to an
AVX2/NEON SIMD codec at runtime (multi-GB/s encode) with a pure-Python
fallback. Callers that base64 genuinely large payloads — PCM audio, PNG
thumbnails / screenshots, f16 embedding vectors — justify the SIMD codec over
the scalar stdlib path.

This module collapses the half-dozen hand-rolled base64 idioms into a handful of
well-named, string-first helpers::

    from unstd.serde import b64

    s = b64.b64s(data)  # bytes  -> str   (standard, ASCII)
    data = b64.b64d(s)  # str|bytes -> bytes (standard, validate=True)
    text = b64.b64text(s)  # str|bytes -> str  (utf-8, errors="replace")
    us = b64.b64u_s(data)  # bytes  -> str   (url-safe, MIME)
    ub = b64.b64u_d(s)  # str|bytes -> bytes (url-safe)
    blob = b64.b64_json(obj)  # any JSON obj -> str  (b64 of a jsonx blob)
    obj = b64.unb64_json(blob)  # str|bytes -> Python  (via serde.jsonx)

Backend & fallback: pybase64 is provided by the ``serde`` extra. When
it is absent (a base ``unstd`` install) every helper transparently uses the
stdlib ``base64`` module — same surface, byte-identical output, scalar speed —
so the module works everywhere. Only :func:`b64s` differs between backends
(pybase64's ``b64encode_as_string`` vs the ``base64.b64encode(x).decode("ascii")``
idiom); every other helper calls an API present in both.

Faithfulness contract — verified byte-for-byte against stdlib ``base64`` in
``tests/serde/test_b64.py``:

- **Standard + url-safe encode** produce the exact same padded bytes stdlib
  emits (``b64s``/``b64u_s`` differ only in that they hand back ``str``, the
  ubiquitous ``base64.b64encode(x).decode("ascii")`` idiom fused into one call).
- **Standard decode** (``b64d``) passes ``validate=True`` so non-alphabet bytes
  raise ``binascii.Error`` instead of being silently discarded — a wire payload
  that isn't clean base64 is a bug, not something to truncate past.
- **Url-safe decode** (``b64u_d``) mirrors stdlib ``urlsafe_b64decode``: the
  lenient, non-validating alphabet used for MIME, still requiring canonical
  padding — pinned via ``padded=True`` on the pybase64 backend, since pybase64
  ``>=1.5`` flipped its own default to unpadded.
- Both decoders accept ``str`` *or* ``bytes`` so callers stop sprinkling
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
    # `_HAVE_PYBASE64`-guarded `b64encode_as_string`), so type-checking against
    # pybase64 alone is faithful for both.
    import pybase64 as _b64

    _HAVE_PYBASE64 = True
else:
    try:
        import pybase64 as _b64

        _HAVE_PYBASE64 = True
    except ImportError:  # base install without the `serde` extra — stdlib fallback
        import base64 as _b64

        _HAVE_PYBASE64 = False


__all__ = [
    "Error",
    "b64_json",
    "b64d",
    "b64s",
    "b64text",
    "b64u_d",
    "b64u_s",
    "b64u_text",
    "unb64_json",
]


def b64s(data: bytes) -> str:
    """Encode ``data`` to an ASCII ``str`` (padded)."""
    if _HAVE_PYBASE64:
        return _b64.b64encode_as_string(data)
    return _b64.b64encode(data).decode("ascii")


def b64d(s: str | bytes) -> bytes:
    """Decode ``s`` to ``bytes``.

    ``validate=True`` — non-alphabet characters raise :data:`Error` rather than
    being discarded, so malformed wire payloads fail loudly.
    """
    return _b64.b64decode(s, validate=True)


def b64text(s: str | bytes, *, errors: str = "replace") -> str:
    """Decode ``s`` and UTF-8 ``decode`` the result to text."""
    return b64d(s).decode("utf-8", errors)


def b64u_s(data: bytes) -> str:
    """URL-/filesystem-safe Base64-encode ``data`` to an ASCII ``str`` (padded).

    The ``-``/``_`` alphabet MIME wants (e.g. Gmail raw-message bodies).
    """
    return _b64.urlsafe_b64encode(data).decode("ascii")


def b64u_d(s: str | bytes) -> bytes:
    """URL-safe Base64-decode ``s`` to ``bytes`` (lenient alphabet, like stdlib).

    Requires canonical padding, matching stdlib's ``urlsafe_b64decode`` — pybase64
    ``>=1.5`` flipped its own default to ``padded=False`` (`"to align with Python
    3.15 behavior"`), so this is pinned explicitly rather than inherited.
    """
    if _HAVE_PYBASE64:
        return _b64.urlsafe_b64decode(s, padded=True)
    return _b64.urlsafe_b64decode(s)


def b64u_text(s: str | bytes, *, errors: str = "replace") -> str:
    """URL-safe Base64-decode ``s`` and UTF-8 ``decode`` the result to text."""
    return b64u_d(s).decode("utf-8", errors)


def b64_json(obj: object) -> str:
    """Encode ``obj`` as a Base64 ``str`` of its compact JSON — the idiom for smuggling a config/arg blob through a single CLI argument or env slot."""
    return b64s(jsonx.dumpb(obj))


def unb64_json(s: str | bytes) -> jsonx.Json:
    """Inverse of :func:`b64_json` — Base64-decode ``s`` then parse the JSON."""
    return jsonx.loads(b64d(s))
