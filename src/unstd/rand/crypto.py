"""Cryptographically-secure randomness — the explicit CSPRNG path.

Backend: this path is **always** the stdlib :mod:`secrets` module (a
CSPRNG over ``os.urandom``) — never numpy, never the seedable draw surface. It is
deliberately *unseedable*: :func:`unstd.rand.seed` does not touch it, because
reproducibility and cryptographic secrecy are mutually exclusive. Making the
security-sensitive path a separate, seed-immune module is the whole point — a
seeded simulation can never accidentally weaken a token.

Reach here for tokens, API keys, salts, nonces, password-reset / OTP codes; reach
for :mod:`unstd.rand.draw` for simulations, sampling, shuffles, and ML / eval runs.

Prior art: stdlib ``secrets`` (PEP 506) — a thin, misuse-resistant wrapper over the
OS CSPRNG (``os.urandom``).
"""

from __future__ import annotations

import secrets as _secrets
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = [
    "below",
    "bits",
    "choice",
    "token_bytes",
    "token_hex",
    "token_urlsafe",
]


def token_bytes(n: int = 32) -> bytes:
    """*n* cryptographically-secure random bytes."""
    return _secrets.token_bytes(n)


def token_hex(n: int = 32) -> str:
    """Return a secure random hex token from *n* bytes (``2 * n`` hex chars)."""
    return _secrets.token_hex(n)


def token_urlsafe(n: int = 32) -> str:
    """Return a secure URL-safe base64 token drawn from *n* random bytes."""
    return _secrets.token_urlsafe(n)


def below(n: int) -> int:
    """Return a secure random int in ``[0, n)`` (uniform, no modulo bias)."""
    return _secrets.randbelow(n)


def bits(k: int) -> int:
    """Return a secure random non-negative int carrying *k* random bits."""
    return _secrets.randbits(k)


def choice[T](seq: Sequence[T]) -> T:
    """Return a single secure, uniformly-random element of *seq*."""
    return _secrets.choice(seq)
