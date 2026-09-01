"""UUID generation — the canonical cross-service UID strategy (RFC 9562).

Two ways to get an id, and the difference is the whole module:

- :func:`new` **mints** a v7 — a millisecond timestamp plus random bits. Two
  calls never agree, which is what you want for an opaque entity id.
- :func:`derive` **computes** a v5 — the RFC 4122 name-based UUID of a name
  inside a namespace. The same inputs always give the same id, which is how a
  provider's own thread id can name a local conversation with no lookup table.

Backend & fallback: the ``ids`` extra provides ``uuid-utils``
(Rust-backed via PyO3, ~10-50x faster than stdlib generation). Unlike BLAKE3,
UUIDv7 *does* have a faithful pure-stdlib construction, so when ``uuid-utils`` is
absent (a base ``unstd`` install) this module falls back to a spec-correct
stdlib generator — same 128-bit layout (48-bit big-endian ms timestamp · version
7 · variant 0b10 · 74 bits split into a 12-bit monotonic counter + 62 random
bits, RFC 9562 §6.2 Method 1 — mirroring the approach CPython 3.14's own
``uuid.uuid7`` added natively), so the surface works everywhere, ids minted
within the same millisecond still sort, and only the generation speed differs.
"""

from __future__ import annotations

import os
import threading
import time
from uuid import UUID, uuid5


# Spec-correct stdlib UUIDv7 fallback — always defined (so it is directly
# testable regardless of which backend `new()`/`new_hex()` dispatch to below),
# and the only path taken on a base install without the `ids` extra.
_counter_lock = threading.Lock()
_last_ms: int | None = None
_last_counter = 0  # 12-bit monotonic counter, RFC 9562 §6.2 Method 1


def _seed_counter() -> int:
    return int.from_bytes(os.urandom(2), "big") & 0xFFF  # fresh 12-bit seed


def _next_ms_and_counter(now_ms: int) -> tuple[int, int]:
    """Advance the module-level ``(timestamp, counter)`` state for one mint.

    RFC 9562 §6.2 Method 1: a counter that only ever increases within one
    millisecond, reseeded to a fresh random value the instant the timestamp
    itself advances. Without this, a hot loop minting many ids inside the
    same millisecond relies on 74 bits of random luck to sort in call order —
    4096 ids/ms is enough headroom that a single-process fallback never
    actually needs the overflow branch, but it exists (and is exercised in
    tests) rather than silently mis-sorting past it.
    """
    global _last_ms, _last_counter
    with _counter_lock:
        if _last_ms is None or now_ms > _last_ms:
            counter = _seed_counter()
        else:
            if now_ms < _last_ms:  # clock stepped backward — hold the line
                now_ms = _last_ms
            counter = _last_counter + 1
            if counter > 0xFFF:  # 12-bit counter exhausted this ms — force the clock on
                now_ms += 1
                counter = _seed_counter()
        _last_ms, _last_counter = now_ms, counter
        return now_ms, counter


def _stdlib_uuid7() -> UUID:
    """RFC 9562 UUIDv7 from stdlib entropy.

    Layout: a 48-bit ms timestamp, a 12-bit monotonic counter (rand_a), then 62
    random bits (rand_b).
    """
    ms, counter = _next_ms_and_counter(time.time_ns() // 1_000_000)
    rand_b = int.from_bytes(os.urandom(8), "big") & (1 << 62) - 1
    value = (ms & (1 << 48) - 1) << 80  # unix_ts_ms  (bits 127..80)
    value |= 0x7 << 76  # version 7   (bits 79..76)
    value |= counter << 64  # counter     (bits 75..64)
    value |= 0b10 << 62  # variant     (bits 63..62)
    value |= rand_b  # rand_b      (bits 61..0)
    return UUID(int=value)


try:
    from uuid_utils import uuid7 as _uuid7

    def _new_uuid7() -> str:
        return str(_uuid7())

    def _new_uuid7_hex() -> str:
        return _uuid7().hex

except (
    ImportError
):  # base install without the `ids` extra — spec-correct stdlib fallback

    def _new_uuid7() -> str:
        return str(_stdlib_uuid7())

    def _new_uuid7_hex() -> str:
        return _stdlib_uuid7().hex


def new() -> str:
    """Generate a time-ordered UUIDv7 string (lowercase, hyphenated)."""
    return _new_uuid7()


def new_hex() -> str:
    """Generate a time-ordered UUIDv7 as a bare 32-char hex string.

    For correlation keys embedded in log lines / metric labels where
    hyphens hurt grep-ability.
    """
    return _new_uuid7_hex()


def parse(s: str) -> UUID:
    """Parse a canonical UUID string, raising ``ValueError`` if it is malformed.

    Here because :func:`derive` takes a namespace and a namespace is written
    down as a string; without this, every caller that wants a derived id has to
    reach past the seam for the stdlib just to spell its own constant.
    """
    return UUID(s)


def derive(namespace: UUID, name: str) -> UUID:
    """The RFC 4122 version-5 UUID of ``name`` inside ``namespace``.

    Deterministic where :func:`new` is not: the same namespace and name yield
    the same id on every call, from any process, forever. That is what lets an
    identity you do not store — a Gmail thread, a phone number, a chat
    handle — name its own conversation without a lookup table, and it is why the
    values this returns are load-bearing. Changing a namespace, changing how a
    caller normalizes ``name``, or changing the digest underneath renames every
    identity derived before it, so a differing value is a defect rather than a
    new expectation.

    Mirrors Go's ``uid.Derive``; the two must agree byte for byte, since both
    planes read and write the same ``email_thread_id`` column. Deliberately on
    stdlib rather than ``uuid-utils``: v5 is a pure function of its inputs with
    no entropy to speed up, and one return type is worth more here than a faster
    hash on a per-inbound-message path.
    """
    return uuid5(namespace, name)
