"""BLAKE3-256 — the one digest, for content addressing and security hashing alike.

The width constants live here rather than in a table of algorithm choices: 32
bytes is BLAKE3's own output width, not a policy someone picked, so it belongs
beside the call it bounds.

``derive_key`` is here rather than in a module of its own because BLAKE3 key
derivation is a *mode of the same digest* — same compression function, different
initial flags — not a separate key-management concern. Splitting it out would
imply a keystore that does not exist.

``equal`` is here because comparing a digest is part of using one.
:mod:`unstd.crypto.token` re-exports it, since a MAC compare is its most common
caller.

``legacy_blake2b`` is the one deliberate exception to "one digest", and it earns
its place by keeping a second algorithm *inside* the seam: an identifier already
minted with BLAKE2b cannot change algorithm without renaming every value derived
from it, so a caller in that position needs somewhere to go other than back to
``hashlib``.

The suite behind this module is driven by the BLAKE3 reference implementation's
own published test vectors, so the answers come from the algorithm's authors
rather than from this code agreeing with itself.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Self

from unstd.crypto import blake3


if TYPE_CHECKING:
    from collections.abc import Iterable


DIGEST_BYTES = 32
"""BLAKE3's output width, and so the ceiling on :func:`hex_n` truncation."""

KDF_BYTES = 32
"""The width of a :func:`derive_key` subkey — BLAKE3's output width again."""


def sum_(data: bytes) -> bytes:
    """BLAKE3-256 digest of *data* (32 bytes)."""
    return bytes(blake3(data).digest())


def hex_(data: bytes) -> str:
    """Full BLAKE3-256 hex digest of *data* (64 chars)."""
    return str(blake3(data).hexdigest())


def hex_n(data: bytes, n: int) -> str:
    """Truncated BLAKE3 hex digest — the first *n* bytes, so ``n * 2`` hex chars.

    Truncation is safe for BLAKE3 (an XOF: every prefix is itself a digest), but
    it costs collision resistance quadratically — *n* = 8 gives a 64-bit digest,
    so only truncate for a short human-facing id, never for a security decision.
    """
    if not 1 <= n <= DIGEST_BYTES:
        msg = f"truncation width must be 1..{DIGEST_BYTES} bytes, got {n}"
        raise ValueError(msg)
    return str(blake3(data).hexdigest()[: n * 2])


def of_parts(parts: Iterable[bytes]) -> bytes:
    """Digest a sequence of byte strings without joining them in memory first.

    The parts are fed in order with no separator, so this is exactly
    ``sum_(b"".join(parts))`` — domain-separate the pieces yourself if their
    boundaries matter.
    """
    return Stream().update_all(parts).sum_()


def derive_key(context: str, material: bytes) -> bytes:
    """Derive a 32-byte subkey from *material* under a domain-separation *context*.

    *context* is what keeps two unrelated uses of the same material from ever
    deriving the same subkey, so it is the argument that carries the security
    property: it should be a constant your program registers once — versioned,
    and specific enough that nobody writes the same string for a different
    purpose — never an attacker-influenced value and never a bare literal
    retyped at each call site.

    BLAKE3 hashes the context separately from the material, so two contexts
    differing by one character give two unrelated keys.
    """
    return bytes(blake3(material, derive_key_context=context).digest())


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


def legacy_blake2b(data: bytes, *, width: int) -> bytes:
    """A persisted BLAKE2b identity that cannot migrate without moving its owners.

    New digests belong on BLAKE3 — :func:`sum_`. This exists for identifiers
    already *defined* in terms of BLAKE2b, where changing the algorithm would
    change every stored key or deterministic schedule at once, and it lives in
    the seam so that case does not become a reason to import ``hashlib`` beside
    it and quietly grow a second digest for new work too.
    """
    return hashlib.blake2b(data, digest_size=width).digest()


class Stream:
    """Incremental BLAKE3-256, for data too big or too lazy to hold at once.

    Reusable after reading — BLAKE3 finalization is non-destructive, so
    :meth:`sum_` can be called mid-stream and updating afterwards still works.
    """

    __slots__ = ("_hasher",)

    def __init__(self) -> None:
        """Start an empty stream — no material absorbed yet."""
        self._hasher = blake3()

    def update(self, chunk: bytes) -> Self:
        """Absorb one chunk; returns self so calls chain."""
        self._hasher.update(chunk)
        return self

    def update_all(self, chunks: Iterable[bytes]) -> Self:
        """Absorb every chunk of an iterable in order; returns self so calls chain."""
        for chunk in chunks:
            self._hasher.update(chunk)
        return self

    def sum_(self) -> bytes:
        """The digest of everything absorbed so far (32 bytes)."""
        return bytes(self._hasher.digest())

    def hex_(self) -> str:
        """The hex digest of everything absorbed so far (64 chars)."""
        return str(self._hasher.hexdigest())


__all__ = [
    "DIGEST_BYTES",
    "KDF_BYTES",
    "Stream",
    "derive_key",
    "equal",
    "hex_",
    "hex_n",
    "legacy_blake2b",
    "of_parts",
    "sum_",
]
