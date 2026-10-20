"""Serialization/deserialization helpers (stdlib-faithful, accelerated).

Each module wraps a best-in-class native codec behind a small, stdlib-faithful
surface. ``jsonx`` uses guarded orjson encoding and msgspec decoding; typed JSON
also uses msgspec. The
accelerated backends live behind the ``serde`` extra (``pip install
'unstd[serde]'``); without it, ``jsonx``/``b64``/``ndjson`` transparently fall
back to the stdlib (slower, same surface) and the typed ``structs`` codecs —
which have no stdlib equivalent — raise a clear ImportError. Import the module,
not its members::

    from unstd.serde import jsonx, b64, structs, ndjson

- ``jsonx``   — orjson/msgspec-backed, stdlib-``json``-faithful JSON codec for hot
  machine-to-machine paths (untyped ``dict``/``list`` round-trips).
- ``b64``     — pybase64 (SIMD ``libbase64``) under a stdlib-``base64``-faithful
  surface; the ``b64encode(x).decode(...)`` idiom fused into named helpers.
- ``structs`` — typed ``msgspec`` codecs: one reusable Encoder/Decoder per wire
  schema, with validate-on-decode and a "malformed → miss" fallback.
- ``ndjson``  — newline-delimited JSON (``.jsonl``) streaming over ``jsonx``
  or a reusable ``structs.Codec`` for validated typed records.
"""

from __future__ import annotations
