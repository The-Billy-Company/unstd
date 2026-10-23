"""Bearer tokens — the keyed-BLAKE3 MAC, constant-time compare, and the claims envelope.

Keyed BLAKE3 is a PRF by construction, so it is a MAC with no HMAC
outer/inner-pad wrapper around it, and it reuses the digest implementation the
rest of this package already leans on. The claims envelope is::

    base64url_nopad(claims_json) "." base64url_nopad(keyed_mac(base64url claims bytes))

The MAC covers the *encoded* payload, not the raw JSON, so a verifier never has
to re-encode to check the tag. That one detail is what a second implementation
of this envelope gets wrong silently, so it is pinned by its own test rather
than left to the whole-token golden.

The MAC comes in the two shapes :mod:`unstd.crypto.digest` names — :func:`mac_raw`
(32 bytes) and :func:`mac_hex` (64 chars) — and :func:`verify_mac` accepts a tag
in either. :func:`equal` is the constant-time compare every tag check rides.

An opaque bearer (no claims inside) is just CSPRNG bytes:
``unstd.rand.crypto.token_urlsafe()``.
"""

from __future__ import annotations

import hmac
import sys
from typing import TYPE_CHECKING

from unstd.crypto._blake3 import _PARALLEL_MIN_BYTES, _parallel_hasher, blake3
from unstd.serde import b64


if TYPE_CHECKING:
    from collections.abc import Callable


SEPARATOR = "."
"""The one byte between payload and tag. Present exactly once in a valid token."""

KEY_BYTES = 32
"""Keyed BLAKE3 takes exactly this much key — not a minimum, an equality."""

TAG_BYTES = 32
"""Width of a MAC tag, which is BLAKE3's output width."""

_TAG_CHARS = 43
_MIN_TOKEN_CHARS = _TAG_CHARS + 3  # At least two payload chars and one separator.
_DEFAULT_MAX_TOKEN_CHARS = 8192
_TWO_CHAR_END = "AQgw"
_THREE_CHAR_END = "AEIMQUYcgkosw048"
_str_len = str.__len__


def equal(a: str | bytes, b: str | bytes) -> bool:
    """Constant-time equality for a token, digest, or MAC.

    Never use ``==`` on one of those: it short-circuits on the first differing
    byte, which leaks the length of the shared prefix and is enough to forge a
    tag one byte at a time. Mismatched str/bytes is False rather than a
    TypeError. Non-ASCII strings are also refused: wire tags are ASCII and
    ``hmac.compare_digest`` otherwise raises for caller-controlled Unicode.
    """
    if isinstance(a, str):
        return (
            isinstance(b, str)
            and a.isascii()
            and b.isascii()
            and hmac.compare_digest(a, b)
        )
    return isinstance(b, bytes) and hmac.compare_digest(a, b)


def _keyed(key: bytes, data: bytes) -> blake3:
    if len(key) != KEY_BYTES:
        msg = f"keyed BLAKE3 needs exactly {KEY_BYTES} bytes of key, got {len(key)}"
        raise ValueError(msg)
    if type(data) is bytes and len(data) >= _PARALLEL_MIN_BYTES:
        return _parallel_hasher(data, key=key)
    return blake3(data, key=key)


def mac_raw(key: bytes, data: bytes) -> bytes:
    """Keyed BLAKE3 MAC of *data* as raw bytes (32). *key* must be exactly 32 bytes."""
    return _keyed(key, data).digest()


def mac_hex(key: bytes, data: bytes) -> str:
    """Keyed BLAKE3 MAC of *data* as hex (64 chars). *key* must be exactly 32 bytes."""
    return _keyed(key, data).digest().hex()


def verify_mac(key: bytes, data: bytes, tag: str | bytes) -> bool:
    """Recompute the MAC over *data* and compare it to *tag* — hex ``str`` or raw ``bytes`` — in constant time."""
    return equal(
        mac_hex(key, data) if isinstance(tag, str) else mac_raw(key, data), tag
    )


def mint(key: bytes, claims: bytes) -> str:
    """Encode *claims* and append its keyed MAC, producing ``payload.tag``.

    *claims* is the already-serialized claims document — this function does not
    serialize for you, because key order in the JSON is part of what the MAC
    covers, and a minter that re-serialized would quietly produce a token a
    stricter verifier rejects.

    Empty *claims* raises rather than minting ``".tag"``: :func:`verify` refuses an
    empty claims segment, because a bearer that asserts nothing grants nothing, so
    minting one would hand back a token that can never be redeemed.

    The verifier defaults to an 8192-character envelope limit. Larger claims
    remain mintable; their verifier must explicitly choose a larger limit or
    ``max_token_chars=None`` after imposing its own request-size bound.
    """
    if not claims:
        msg = (
            "refusing to mint a token with no claims — an empty bearer asserts nothing"
        )
        raise ValueError(msg)
    payload = b64.encode(claims, url=True, pad=False)
    tag = mac_raw(key, payload.encode("ascii"))
    return payload + SEPARATOR + b64.encode(tag, url=True, pad=False)


def verify(
    key: bytes, token: str, *, max_token_chars: int | None = _DEFAULT_MAX_TOKEN_CHARS
) -> bytes | None:
    """Return the claims bytes if *token*'s MAC checks out under *key*, else None.

    Fails closed on every shape problem — a missing or extra separator, a
    noncanonical base64url segment, a tag of the wrong width — and compares the tag in
    constant time. None means "do not trust this"; malformed token text never
    raises. Invalid signer keys remain configuration errors for canonical
    envelopes within the chosen size limit.

    The complete envelope, including separator and tag, is limited to 8192
    characters before slicing, decoding, or hashing. Set *max_token_chars* to a
    positive integer for a different bound, or explicitly to None when the
    caller already bounds requests or handles large trusted envelopes. Invalid
    limit types and nonpositive limits raise TypeError and ValueError.
    """
    if max_token_chars is not None:
        # The actual default is already a validated exact int. Identity avoids
        # repeating configuration checks without accepting equal floats or bools.
        if max_token_chars is not _DEFAULT_MAX_TOKEN_CHARS:
            if type(max_token_chars) is not int:
                msg = "max_token_chars must be a positive integer or None"
                raise TypeError(msg)
            if max_token_chars <= 0:
                msg = "max_token_chars must be a positive integer or None"
                raise ValueError(msg)
        token_chars = _str_len(token)
        if token_chars < _MIN_TOKEN_CHARS or token_chars > max_token_chars:
            return None
    if type(token) is str:
        if max_token_chars is None and _str_len(token) < _MIN_TOKEN_CHARS:
            return None
        # The full tag has a fixed encoded width, so the only possible separator
        # is 44 characters from the end. Wrong-width tags and absent/appended
        # separators fail before a search or either substring allocation.
        if token[-44] != SEPARATOR:
            return None
        payload = token[:-44]
        tag = token[-43:]
    else:
        # Preserve a string subclass's partition hooks on the compatible path;
        # the bounded path above still uses its actual stored string length.
        payload, _, tag = token.partition(SEPARATOR)
        if len(tag) != _TAG_CHARS or not payload:
            return None
    # Unpadded Base64 has one spelling precisely when its alphabet, length,
    # and unused terminal bits are canonical. Check those directly instead of
    # allocating decoded claims and two re-encodings before authentication.
    remainder = len(payload) % 4
    if (
        remainder == 1
        or (remainder == 2 and payload[-1] not in _TWO_CHAR_END)
        or (remainder == 3 and payload[-1] not in _THREE_CHAR_END)
        or tag[-1] not in _THREE_CHAR_END
        or not payload.isascii()
        or not tag.isascii()
        or not payload.replace("-", "a").replace("_", "a").isalnum()
        or not tag.replace("-", "a").replace("_", "a").isalnum()
    ):
        return None
    try:
        got = b64.decode(tag, url=True)
    except ValueError:  # binascii.Error and UnicodeEncodeError are both ValueErrors
        return None
    payload_bytes = payload.encode("ascii")
    if not equal(got, mac_raw(key, payload_bytes)):
        return None
    return b64.decode(payload_bytes, url=True)


# Reuse a helper already loaded by digest without imposing its module-load cost
# on token-only imports. Explicit setup below can select it for a token service.
# Neither path caches signer keys, messages or BLAKE3 hashers, and no request
# probes for an optional backend or changes the chosen implementation.
_verify_python = verify
_loaded_native = sys.modules.get(f"{__package__}._stream_native")
_loaded_bind = getattr(_loaded_native, "bind_token_verifier", None)
_native_verify: Callable[[bytes, str, int | None], bytes | None]
if _loaded_bind is not None:
    _native_verify = _loaded_bind(_verify_python)

    def _verify_accelerated(
        key: bytes,
        token: str,
        *,
        max_token_chars: int | None = _DEFAULT_MAX_TOKEN_CHARS,
    ) -> bytes | None:
        return _native_verify(key, token, max_token_chars)

    _verify_accelerated.__name__ = "verify"
    _verify_accelerated.__qualname__ = "verify"
    _verify_accelerated.__doc__ = _verify_python.__doc__
    verify = _verify_accelerated


def prepare_verifier(
    *,
    max_token_chars: int | None = _DEFAULT_MAX_TOKEN_CHARS,
) -> Callable[[bytes, str], bytes | None]:
    """Prepare a reusable verifier with an explicit envelope-size policy.

    Validate the limit and load the optional parser during this setup call.
    The returned function accepts ``(key, token)`` and retains only the chosen
    limit and backend references; signer keys and messages stay per request.
    Compiler-free installs retain the Python verifier and the same policy.

    A token-only import keeps its direct Python path. Applications doing many
    verifications can call this once during startup to pay optional native
    loading and binding costs explicitly, before handling requests.
    """
    if max_token_chars is not None:
        if type(max_token_chars) is not int:
            msg = "max_token_chars must be a positive integer or None"
            raise TypeError(msg)
        if max_token_chars <= 0:
            msg = "max_token_chars must be a positive integer or None"
            raise ValueError(msg)
    try:
        from unstd.crypto._stream_native import bind_token_verifier
    except ImportError:

        def prepared(key: bytes, token: str) -> bytes | None:
            return _verify_python(key, token, max_token_chars=max_token_chars)

    else:
        helper = bind_token_verifier(_verify_python)

        def prepared(key: bytes, token: str) -> bytes | None:
            return helper(key, token, max_token_chars)

    return prepared


__all__ = [
    "KEY_BYTES",
    "SEPARATOR",
    "TAG_BYTES",
    "equal",
    "mac_hex",
    "mac_raw",
    "mint",
    "prepare_verifier",
    "verify",
    "verify_mac",
]
