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


__all__ = [
    "below",
    "bits",
    "choice",
    "token_bytes",
    "token_hex",
    "token_urlsafe",
]


token_bytes = _secrets.token_bytes
"""*n* (default 32) cryptographically-secure random bytes."""

token_hex = _secrets.token_hex
"""A secure random hex token from *n* (default 32) bytes — ``2 * n`` hex chars."""

token_urlsafe = _secrets.token_urlsafe
"""A secure URL-safe base64 token from *n* (default 32) random bytes — an opaque bearer."""

below = _secrets.randbelow
"""A secure random int in ``[0, n)`` (uniform, no modulo bias)."""

bits = _secrets.randbits
"""A secure random non-negative int carrying *k* random bits."""

choice = _secrets.choice
"""A single secure, uniformly-random element of a non-empty sequence."""
