"""Identifier generation — one hasher, one UID strategy for the whole program.

- ``hash`` — BLAKE3 digests, a thin caller of :mod:`unstd.crypto.digest` (one
  implementation, an identifier-shaped vocabulary over it). No stdlib
  equivalent; hard-requires ``blake3`` (the ``crypto`` extra).
- ``uid`` — UUID minting and derivation (RFC 9562 / RFC 4122). ``new`` mints a
  time-ordered v7; ``derive`` computes the name-based v5 that lets an identity
  your program does not store name its own row. The ``ids`` extra provides the
  Rust-backed ``uuid-utils`` for minting; a spec-correct stdlib fallback keeps
  the base install functional.

Import the module, not its members::

    from unstd.ids import hash, uid
"""

from __future__ import annotations
