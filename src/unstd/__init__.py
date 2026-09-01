"""unstd — the unstandardized library.

Best-in-class native codecs behind small, **stdlib-faithful** surfaces: migrate a
call site by swapping the import, not the call. The base install is pure stdlib —
depending on ``unstd`` costs zero third-party closure — and each accelerated
backend rides an optional extra with a guarded stdlib fallback, so a lightweight
consumer never inherits a heavy dependency graph.

Module groups (grown incrementally from the stdlib census) — each named for the
stdlib surface it stands in for, and each either degrading to that surface or
failing loud where no faithful substitute exists:

- ``serde`` (``json`` / ``base64``) — ``jsonx`` (orjson) · ``b64`` (pybase64) ·
  ``ndjson`` (newline-delimited, rides ``jsonx``) · ``structs`` (msgspec, no
  stdlib equivalent). Behind the ``serde`` extra; the rest fall back to stdlib.
- ``ids`` (``hashlib`` / ``uuid``) — ``hash`` (BLAKE3, fails loud) · ``uid``
  (UUIDv7, falls back to a stdlib generator). Behind the ``ids`` extra.
- ``crypto`` — BLAKE3 digests, key derivation, keyed MACs and the bearer
  envelope, constant-time compare, and a verifying TLS client policy. No
  fallback: ``hashlib`` ships BLAKE2, which is a different algorithm, not a
  slower one.
- ``time`` — ``timeutil`` (pure-stdlib clock seam, always available; its
  protobuf ``Timestamp`` bridge is behind the ``proto`` extra) · ``dateutil``
  (the ``whenever`` typed-instant seam, behind the ``time`` extra).
- ``model`` — the strict Pydantic wire-shape spine: ``StrictModel`` ·
  ``FrozenModel`` · ``StrictSettings``. Behind the ``model`` extra (pydantic has
  no stdlib equivalent, so there is no fallback).
- ``rex`` (``re``) — irregex linear-time matching, falls back to ``re``; the
  flag and type vocabulary is re-exported verbatim.
- ``containers`` (``collections``) — always-sorted collections and a persistent
  HAMT ``Map``. No faithful fallback (an ``O(n)`` stand-in would be a silent
  lie), so the placeholders raise on construction.
- ``rand`` (``random``) — numpy PCG64 bulk draws; security-sensitive draws stay
  on ``secrets`` regardless of the extra.
- ``pack`` (``struct``) — numpy bulk pack/unpack.
- ``clone`` (``copy``) — msgspec round-trip, falls back to ``copy.deepcopy``.
- ``text`` (``difflib``) — rapidfuzz similarity and fuzzy matching.
- ``audio`` (``wave``) — soundfile read/write plus a streaming writer.
- ``toml`` (``tomllib``) — reading is stdlib; writing needs tomlkit.
- ``proc`` (``subprocess``) · ``fs`` (``os``) · ``iters`` (``itertools``) —
  pure stdlib, no extra, always present in the base install.

Import the module, not its members::

    from unstd.serde import jsonx, b64, structs, ndjson
    from unstd.ids import hash, uid
    from unstd.time import timeutil, dateutil
    from unstd.model import StrictModel, FrozenModel, StrictSettings
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version


try:
    __version__ = _installed_version("unstd")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0.dev0"
