"""The BLAKE3 backend, imported once for :mod:`digest` and :mod:`token`.

BLAKE3 has **no stdlib equivalent** — ``hashlib`` ships BLAKE2b, a *different*
algorithm, so a silent fallback would fork the digest rather than slow it down.
The guard lives here, not in the package ``__init__``, so the pure-stdlib
:mod:`unstd.crypto.tls` still imports on a base install.
"""

from __future__ import annotations


try:
    from blake3 import blake3
except ImportError as exc:  # fail loud, don't fork the digest
    msg = "unstd.crypto.digest/token require the 'crypto' extra — install with: pip install 'unstd[crypto]'"
    raise ImportError(msg) from exc


__all__ = ["blake3"]
