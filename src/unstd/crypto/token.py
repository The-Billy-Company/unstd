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

from unstd.crypto import blake3
from unstd.serde import b64


SEPARATOR = "."
"""The one byte between payload and tag. Present exactly once in a valid token."""

KEY_BYTES = 32
"""Keyed BLAKE3 takes exactly this much key — not a minimum, an equality."""

TAG_BYTES = 32
"""Width of a MAC tag, which is BLAKE3's output width."""


def equal(a: str | bytes, b: str | bytes) -> bool:
    """Constant-time equality for a token, digest, or MAC.

    Never use ``==`` on one of those: it short-circuits on the first differing
    byte, which leaks the length of the shared prefix and is enough to forge a
    tag one byte at a time. Mismatched str/bytes is False rather than a
    TypeError, so a caller cannot turn a comparison into a 500.
    """
    if isinstance(a, str):
        return isinstance(b, str) and hmac.compare_digest(a, b)
    return isinstance(b, bytes) and hmac.compare_digest(a, b)


def mac_raw(key: bytes, data: bytes) -> bytes:
    """Keyed BLAKE3 MAC of *data* as raw bytes (32). *key* must be exactly 32 bytes."""
    if len(key) != KEY_BYTES:
        raise ValueError(_key_error(key))
    return blake3(data, key=key).digest()


def mac_hex(key: bytes, data: bytes) -> str:
    """Keyed BLAKE3 MAC of *data* as hex (64 chars). *key* must be exactly 32 bytes."""
    if len(key) != KEY_BYTES:
        raise ValueError(_key_error(key))
    return blake3(data, key=key).hexdigest()


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
    """
    if not claims:
        msg = (
            "refusing to mint a token with no claims — an empty bearer asserts nothing"
        )
        raise ValueError(msg)
    payload = b64.encode(claims, url=True, pad=False)
    tag = mac_raw(key, payload.encode("ascii"))
    return payload + SEPARATOR + b64.encode(tag, url=True, pad=False)


def verify(key: bytes, token: str) -> bytes | None:
    """Return the claims bytes if *token*'s MAC checks out under *key*, else None.

    Fails closed on every shape problem — a missing or extra separator, a
    non-base64 segment, a tag of the wrong width — and compares the tag in
    constant time. None means "do not trust this"; it never raises for
    caller-supplied input.
    """
    payload, sep, tag = token.partition(SEPARATOR)
    if not sep or SEPARATOR in tag or not payload or not tag:
        return None
    try:
        claims = b64.decode(payload, url=True)
        got = b64.decode(tag, url=True)
    except ValueError:  # binascii.Error and UnicodeEncodeError are both ValueErrors
        return None
    if len(got) != TAG_BYTES or not equal(got, mac_raw(key, payload.encode("ascii"))):
        return None
    return claims


def _key_error(key: bytes) -> str:
    return f"keyed BLAKE3 needs exactly {KEY_BYTES} bytes of key, got {len(key)}"


__all__ = [
    "KEY_BYTES",
    "SEPARATOR",
    "TAG_BYTES",
    "equal",
    "mac_hex",
    "mac_raw",
    "mint",
    "verify",
    "verify_mac",
]
