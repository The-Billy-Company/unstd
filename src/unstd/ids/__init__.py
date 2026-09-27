"""Identifier generation — one UID strategy for the whole program.

- ``uid`` — UUID minting and derivation (RFC 9562 / RFC 4122). ``new`` mints a
  time-ordered v7; ``derive`` computes the name-based v5 that lets an identity
  your program does not store name its own row. The ``ids`` extra provides the
  Rust-backed ``uuid-utils`` for minting; a spec-correct stdlib fallback keeps
  the base install functional.

Content-addressed identifiers are BLAKE3 digests, and live with the digest in
:mod:`unstd.crypto.digest` (``digest.hex(data)`` / ``digest.hex(data, 8)``).

Import the module, not its members::

    from unstd.ids import uid
"""

from __future__ import annotations
