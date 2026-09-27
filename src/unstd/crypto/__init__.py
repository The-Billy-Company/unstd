"""One cryptographic seam: BLAKE3 digests, keyed MACs, and a verifying TLS policy.

Three modules, each deep enough to be the only place its subject is spelled:

- ``digest`` — BLAKE3-256 content hashing (``raw`` / ``hex`` / truncated /
  streaming) and ``derive_key``.
- ``token`` — the keyed-BLAKE3 MAC, constant-time ``equal``, and the claims envelope.
- ``tls`` — a verifying TLS client context with no way to ask it not to verify.

Import the module, not its members::

    from unstd.crypto import digest, token, tls

Backend: BLAKE3 has **no stdlib equivalent** — ``hashlib`` ships BLAKE2b, a
*different* algorithm, so a silent fallback would fork the digest rather than
slow it down, and every value already addressed by one would stop resolving.
``digest`` and ``token`` therefore hard-require ``blake3`` (the ``crypto``
extra) and raise an ImportError naming the extra rather than a bare
``ModuleNotFoundError``. ``tls`` is pure stdlib and imports on a base install.
"""
