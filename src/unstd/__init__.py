"""unstd — the unstandardized library.

Native codecs behind small, **stdlib-compatible** surfaces, alongside focused
utility helpers. Documented stdlib replacements preserve their call surface;
additional helpers have explicit contracts, including their divergences.
The base install is pure stdlib — depending on ``unstd`` costs zero third-party
closure — and each accelerated backend rides an optional extra with a guarded
stdlib fallback, so a lightweight consumer never inherits a heavy dependency graph.

Module groups (grown incrementally from the stdlib census) — each named for the
stdlib surface it stands in for, and each either degrading to that surface or
failing loud where no faithful substitute exists:

- ``serde`` (``json`` / ``base64``) — ``jsonx`` (orjson/msgspec) · ``b64`` (pybase64) ·
  ``ndjson`` (newline-delimited, rides ``jsonx``) · ``structs`` (msgspec, no
  stdlib equivalent). Behind the ``serde`` extra; the rest fall back to stdlib.
- ``ids`` (``uuid``) — ``uid``: UUIDv7 minting and v5 derivation, falling back to
  a stdlib generator. Behind the ``ids`` extra.
- ``crypto`` (``hashlib`` / ``hmac`` / ``ssl``) — BLAKE3 digests (content
  hashes and short ids included), key derivation, keyed MACs and the bearer
  envelope, constant-time compare, and a verifying TLS client policy. No
  fallback: ``hashlib`` ships BLAKE2, which is a different algorithm, not a
  slower one.
- ``time`` — ``timeutil`` (clocks, RFC-3339 formatting, ISO parsing, instant
  arithmetic — pure stdlib; its protobuf ``Timestamp`` bridge is behind the
  ``proto`` extra) · ``zoned`` (DST-correct wall-clock time over ``whenever``,
  behind the ``time`` extra).
- ``model`` — the strict Pydantic wire-shape spine: ``StrictModel`` ·
  ``FrozenModel`` · ``StrictSettings``. Behind the ``model`` extra (pydantic has
  no stdlib equivalent, so there is no fallback).
- ``math`` — focused NumPy operations for production numeric and dataframe
  calculations, behind the ``math`` extra.
- ``rex`` (``re``) — irregex linear-time matching, falls back to ``re``; the
  flag and type vocabulary is re-exported verbatim.
- ``containers`` (``collections``) — always-sorted collections and a persistent
  HAMT ``Map``. No faithful fallback (an ``O(n)`` stand-in would be a silent
  lie), so the placeholders raise on construction.
- ``rand`` (``random``) — numpy PCG64 bulk draws; security-sensitive draws stay
  on ``secrets`` regardless of the extra.
- ``pack`` (``struct``) — NumPy bulk pack/unpack and validated immutable numeric
  frames, with optional native geometry, boundary and finite-value scans.
- ``clone`` (``copy``) — an exact-type structural deep clone with optional
  bundled C acceleration and no third-party runtime dependency.
- ``cache`` (``sqlite3``) — persistent JSON results or typed codec artifacts with TTLs, namespaces,
  optional FIFO bounds, atomic batch operations, and concurrent key-coordinated
  producers across local processes.
  Uses ``serde.jsonx`` and requires no extra.
- ``text`` (``difflib``) — rapidfuzz similarity and fuzzy matching.
- ``audio`` (``wave``) — soundfile read/write plus a streaming writer.
- ``toml`` (``tomllib``) — reading is stdlib; writing needs tomlkit.
- ``yaml`` — safe YAML 1.2 and comment-preserving edits; requires the ``yaml`` extra.
- ``compress`` (``gzip`` / ``zlib`` / archive modules) — native compression,
  bounded inflation and CRC behind ``gzipx``; the ``compress`` extra retains
  stdlib framing and a base fallback. ``zstd`` adds the original ``zstandard``
  provider; ``archives`` adds the original ``libarchive`` binding and requires
  a separately installed native libarchive library.
- ``proc`` (``subprocess``) · ``fs`` (``os``) — operating-system helpers with
  optional bundled CPython acceleration and no third-party runtime dependency.
- ``iters`` (``itertools``) — pure stdlib combinators, always in the base install.

Import the module, not its members::

    from unstd.serde import jsonx, b64, structs, ndjson
    from unstd.ids import uid
    from unstd.crypto import digest, token
    from unstd.time import timeutil, zoned
    from unstd.model import StrictModel, FrozenModel, StrictSettings
"""

from __future__ import annotations


__version__: str

# A reload should refresh a version cached by a previous attribute access.
globals().pop("__version__", None)


def __getattr__(name: str) -> str:
    """Resolve and cache the installed version only when it is requested."""
    if name != "__version__":
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    # Metadata discovery imports a large stdlib closure and scans distributions.
    # Submodule consumers (including native codecs) do not need to pay that cost.
    from importlib.metadata import PackageNotFoundError, version

    try:
        resolved = version("unstd")
    except PackageNotFoundError:  # source tree that was never installed
        resolved = "0.0.0.dev0"
    globals()[name] = resolved
    return resolved


def __dir__() -> list[str]:
    """Include the lazy version attribute without resolving install metadata."""
    return sorted({*globals(), "__version__"})
