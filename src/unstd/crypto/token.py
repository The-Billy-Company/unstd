"""Bearer tokens — the keyed-BLAKE3 MAC, constant-time compare, and the claims envelope.

Keyed BLAKE3 is a PRF by construction, so it is a MAC with no HMAC
outer/inner-pad wrapper around it, and it reuses the digest implementation the
rest of this package already leans on. The claims envelope is::

    base64url_nopad(claims_json) "." base64url_nopad(keyed_mac(base64url claims bytes))

The MAC covers the *encoded* payload, not the raw JSON, so a verifier never has
to re-encode to check the tag. That one detail is what a second implementation
of this envelope gets wrong silently, so it is pinned by its own test rather
than left to the whole-token golden.

Base64 here is stdlib ``base64`` rather than :mod:`unstd.serde.b64` on purpose:
the wire format is unpadded, ``serde.b64`` is padded, and the seam should not
inherit an optional extra to strip two ``=`` off a string.
"""

from __future__ import annotations

import base64

from unstd.crypto import blake3
from unstd.crypto.digest import equal
from unstd.rand import crypto as _csprng


SEPARATOR = "."
"""The one byte between payload and tag. Present exactly once in a valid token."""

KEY_BYTES = 32
"""Keyed BLAKE3 takes exactly this much key — not a minimum, an equality."""

TAG_BYTES = 32
"""Width of a MAC tag, which is BLAKE3's output width."""


def mac(key: bytes, data: bytes) -> str:
    """Keyed BLAKE3 MAC of *data* as hex (64 chars). *key* must be exactly 32 bytes."""
    return str(blake3(data, key=_checked(key)).hexdigest())


def mac_bytes(key: bytes, data: bytes) -> bytes:
    """Keyed BLAKE3 MAC of *data* as raw bytes (32). *key* must be exactly 32 bytes."""
    return bytes(blake3(data, key=_checked(key)).digest())


def verify_mac(key: bytes, data: bytes, tag: str) -> bool:
    """Recompute the MAC over *data* and compare it to *tag* in constant time."""
    return equal(mac(key, data), tag)


def opaque(n: int = KEY_BYTES) -> str:
    """Mint an opaque bearer — *n* CSPRNG bytes, URL-safe base64, no claims inside.

    Delegates to :mod:`unstd.rand.crypto` (always stdlib ``secrets``, never
    seedable) so there is one randomness door in the program, not two.
    """
    return _csprng.token_urlsafe(n)


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
    payload = _encode(claims)
    return payload + SEPARATOR + _encode(mac_bytes(key, payload.encode("ascii")))


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
        claims = _decode(payload)
        got = _decode(tag)
    except (ValueError, UnicodeEncodeError):
        return None
    if len(got) != TAG_BYTES:
        return None
    if not equal(got, mac_bytes(key, payload.encode("ascii"))):
        return None
    return claims


def _checked(key: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        msg = f"keyed BLAKE3 needs exactly {KEY_BYTES} bytes of key, got {len(key)}"
        raise ValueError(msg)
    return key


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(segment: str) -> bytes:
    # Unpadded on the wire; stdlib insists on a multiple of four, so pad it back.
    return base64.urlsafe_b64decode(
        segment.encode("ascii") + b"=" * (-len(segment) % 4)
    )


__all__ = [
    "KEY_BYTES",
    "SEPARATOR",
    "TAG_BYTES",
    "equal",
    "mac",
    "mac_bytes",
    "mint",
    "opaque",
    "verify",
    "verify_mac",
]
