"""Identifier digests — the identifier-shaped door onto :mod:`unstd.crypto.digest`.

One BLAKE3 implementation, two vocabularies: :mod:`unstd.crypto.digest` is the
cryptographic seam, and these three functions are what an *identifier*
call site wants to say — ``content_hash`` for a content-addressed key, ``hex_n``
for a short human-facing id.

The MAC half used to live here and moved out, because a MAC is not an identifier:
``mac`` / ``verify_mac`` / ``equal`` are now :mod:`unstd.crypto.token`, and
``derive_key`` is :mod:`unstd.crypto.digest`.

Backend: BLAKE3 has **no stdlib equivalent** — ``hashlib`` ships
BLAKE2b, a *different* algorithm, so a silent fallback would fork the digest and
corrupt cross-service content-addressing. ``unstd.crypto`` therefore
hard-requires ``blake3`` and, imported without it, raises a clear ImportError
pointing at the extra rather than a bare ``ModuleNotFoundError``.
"""

from __future__ import annotations

from unstd.crypto import digest as _digest


def sum_(data: bytes) -> bytes:
    """BLAKE3-256 digest of *data* (32 bytes)."""
    return _digest.sum_(data)


def hex_n(data: bytes, n: int) -> str:
    """Return a truncated BLAKE3 hex digest — first *n* x 2 hex chars."""
    return _digest.hex_n(data, n)


def content_hash(data: bytes) -> str:
    """Full BLAKE3-256 hex digest (64 chars) for content-addressable storage."""
    return _digest.hex_(data)


__all__ = ["content_hash", "hex_n", "sum_"]
