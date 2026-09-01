"""One cryptographic seam: BLAKE3 digests, keyed MACs, and a verifying TLS policy.

Three modules, each deep enough to be the only place its subject is spelled:

- ``digest`` — BLAKE3-256 content hashing (bytes / hex / truncated / streaming),
  ``derive_key``, and constant-time ``equal``.
- ``token`` — the keyed-BLAKE3 MAC, opaque bearers, and the claims envelope.
- ``tls`` — a verifying TLS client context with no way to ask it not to verify.

Import the module, not its members::

    from unstd.crypto import digest, token, tls

Backend: BLAKE3 has **no stdlib equivalent** — ``hashlib`` ships BLAKE2b, a
*different* algorithm, so a silent fallback would fork the digest rather than
slow it down, and every value already addressed by one would stop resolving.
This package therefore hard-requires ``blake3`` (the ``crypto`` extra) and
raises an ImportError naming the extra rather than a bare ``ModuleNotFoundError``.
``tls`` is pure stdlib and rides the same extra only because it lives here.
"""

from __future__ import annotations


try:
    from blake3 import blake3
except (
    ImportError
) as exc:  # BLAKE3 has no stdlib equivalent — fail loud, don't fork the digest
    msg = "unstd.crypto requires the 'crypto' extra — install with: pip install 'unstd[crypto]'"
    raise ImportError(msg) from exc


__all__ = ["blake3"]
