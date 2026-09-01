"""Transport policy — one verifying TLS client context, with no way to opt out.

A TLS context assembled at its call site looks reasonable in review and is the
usual home of ``verify=False``: the flag gets added for one local sidecar, and
nothing keeps the next caller from copying the line. Naming the policy once, in
a function that has no parameter for skipping verification, is the whole point.

This module is pure stdlib. It lives under :mod:`unstd.crypto` because transport
security is the same subject as the rest of the seam, and it rides the ``crypto``
extra only because it shares the package with BLAKE3.
"""

from __future__ import annotations

import ssl


type TlsContext = ssl.SSLContext
"""The context type, re-exported so a caller can annotate without importing ``ssl``."""


MINIMUM_VERSION = ssl.TLSVersion.TLSv1_3
"""The floor this policy asserts by default.

1.3 closes the classical-downgrade path, and it is also the required transport
for hybrid post-quantum key exchange (X25519MLKEM768) on OpenSSL >= 3.5 — TLS
1.2 has no hybrid-PQC equivalent, so accepting a downgrade silently drops that
protection too. Every counterparty worth dialing has negotiated 1.3 since 2018.
"""

LEGACY_MINIMUM_VERSION = ssl.TLSVersion.TLSv1_2
"""The floor for a peer that has *not* been shown to negotiate 1.3.

Naming it is the point. A service dials both kinds of peer, and the honest
difference between them is one argument at one call site — not a second
hand-rolled context, which is where the flag that turns verification off
eventually gets added.
"""

_FLOOR = ssl.TLSVersion.TLSv1_2
"""Below this there is nothing left to negotiate down to, so it is refused."""


def client_context(
    *,
    cafile: str | None = None,
    certfile: str | None = None,
    keyfile: str | None = None,
    minimum_version: ssl.TLSVersion = MINIMUM_VERSION,
) -> ssl.SSLContext:
    """A verifying TLS client context — one policy, not one per transport.

    Hostname checking and certificate verification are on (stdlib's
    ``create_default_context`` defaults, restated here so a future edit has to
    argue with them). *cafile* pins a private root, which is how a self-signed
    sidecar certificate is trusted without weakening the public path. Passing
    *certfile* + *keyfile* turns it into mTLS; either one alone is not mTLS and
    loads no chain, rather than half of one.

    *minimum_version* is the one dial, and it stops at
    :data:`LEGACY_MINIMUM_VERSION` — TLS 1.0 and 1.1 raise rather than quietly
    widening what the process will speak. There is deliberately no
    ``verify=False`` / ``insecure=True`` parameter: a seam that can be asked to
    skip verification is a seam that eventually is.
    """
    if minimum_version < _FLOOR:
        msg = f"TLS floor {minimum_version.name} is below {_FLOOR.name}; it will not be honored here"
        raise ValueError(msg)
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.minimum_version = minimum_version
    if certfile and keyfile:
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    return ctx


__all__ = ["LEGACY_MINIMUM_VERSION", "MINIMUM_VERSION", "TlsContext", "client_context"]
