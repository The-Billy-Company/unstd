"""BLAKE3-256 — the one digest, for content addressing and security hashing alike.

Two verbs cover every one-shot use, named for what they hand back::

    from unstd.crypto import digest

    digest.raw(data)  # 32 bytes
    digest.hex(data)  # 64 hex chars — a content-addressed key
    digest.hex(data, 8)  # 16 hex chars — a short human-facing id

:class:`Stream` is the same digest over data too big or too lazy to hold at once,
and the way to digest several parts without joining them first.

The width constant lives here rather than in a table of algorithm choices: 32
bytes is BLAKE3's own output width, not a policy someone picked, so it belongs
beside the call it bounds.

``derive_key`` is here rather than in a module of its own because BLAKE3 key
derivation is a *mode of the same digest* — same compression function, different
initial flags — not a separate key-management concern. Splitting it out would
imply a keystore that does not exist.

``legacy_blake2b`` is the one deliberate exception to "one digest", and it earns
its place by keeping a second algorithm *inside* the seam: an identifier already
minted with BLAKE2b cannot change algorithm without renaming every value derived
from it, so a caller in that position needs somewhere to go other than back to
``hashlib``.

Constant-time comparison of a digest or tag is :func:`unstd.crypto.token.equal`.

The suite behind this module is driven by the BLAKE3 reference implementation's
own published test vectors, so the answers come from the algorithm's authors
rather than from this code agreeing with itself.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Self

from unstd.crypto._blake3 import blake3


if TYPE_CHECKING:
    from collections.abc import Iterable


DIGEST_BYTES = 32
"""BLAKE3's output width — the ceiling on :func:`hex` truncation and the width of a :func:`derive_key` subkey."""


def _hexdigest(hasher: blake3, n: int | None) -> str:
    """*hasher*'s full hex digest, or its first *n* bytes' — the one width check both :func:`hex` spellings share."""
    if n is None:
        return hasher.hexdigest()
    if not 1 <= n <= DIGEST_BYTES:
        msg = f"truncation width must be 1..{DIGEST_BYTES} bytes, got {n}"
        raise ValueError(msg)
    return hasher.hexdigest(length=n)


def raw(data: bytes) -> bytes:
    """BLAKE3-256 digest of *data* (32 bytes)."""
    return blake3(data).digest()


def hex(data: bytes, n: int | None = None) -> str:
    """BLAKE3 hex digest of *data* — all 32 bytes (64 chars), or the first *n* bytes (``2n`` chars).

    Truncation is safe for BLAKE3 (an XOF: every prefix is itself a digest, so
    this asks the hasher for *n* bytes rather than slicing a full one), but it
    costs collision resistance quadratically — *n* = 8 gives a 64-bit digest, so
    only truncate for a short human-facing id, never for a security decision.
    """
    return _hexdigest(blake3(data), n)


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
    return blake3(material, derive_key_context=context).digest()


def legacy_blake2b(data: bytes, *, width: int) -> bytes:
    """A persisted BLAKE2b identity that cannot migrate without moving its owners.

    New digests belong on BLAKE3 — :func:`raw`. This exists for identifiers
    already *defined* in terms of BLAKE2b, where changing the algorithm would
    change every stored key or deterministic schedule at once, and it lives in
    the seam so that case does not become a reason to import ``hashlib`` beside
    it and quietly grow a second digest for new work too.
    """
    return hashlib.blake2b(data, digest_size=width).digest()


class Stream:
    """Incremental BLAKE3-256, for data too big or too lazy to hold at once.

    ``Stream(parts)`` absorbs an iterable of chunks in order with no separator, so
    ``Stream(parts).raw()`` is exactly ``raw(b"".join(parts))`` without the join —
    domain-separate the pieces yourself if their boundaries matter. Reusable after
    reading: BLAKE3 finalization is non-destructive, so :meth:`raw` can be called
    mid-stream and updating afterwards still works.
    """

    __slots__ = ("_hasher",)

    def __init__(self, chunks: Iterable[bytes] = ()) -> None:
        """Start a stream, absorbing *chunks* (if any) in order."""
        self._hasher = blake3()
        for chunk in chunks:
            self._hasher.update(chunk)

    def update(self, chunk: bytes) -> Self:
        """Absorb one chunk; returns self so calls chain."""
        self._hasher.update(chunk)
        return self

    def raw(self) -> bytes:
        """The digest of everything absorbed so far (32 bytes)."""
        return self._hasher.digest()

    def hex(self, n: int | None = None) -> str:
        """The hex digest of everything absorbed so far — see :func:`hex` for *n*."""
        return _hexdigest(self._hasher, n)


__all__ = ["DIGEST_BYTES", "Stream", "derive_key", "hex", "legacy_blake2b", "raw"]
