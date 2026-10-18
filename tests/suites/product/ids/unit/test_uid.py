"""Vectors and adverse cases for ``unstd.ids.uid``.

The two halves of the module fail in opposite directions, so they are pinned in
opposite directions:

- :func:`uid.new` must never repeat, and must carry the v7 shape the whole fleet
  sorts on;
- :func:`uid.derive` must never *change*. Its past output is persisted — a Gmail
  thread, an email correspondence, a chat handle all name their conversation by
  deriving it again — so a differing value renames live rows rather than minting
  a new one.

The v5 answers here are authored from RFC 4122, not read back out of this
implementation. That is what lets a second implementation in another language
pin the same values and land on the same derived id, rather than two that happen
to agree today. Assertions are deliberately strict — never weakened to go green.
"""

from __future__ import annotations

import threading
from uuid import RFC_4122, UUID

import pytest

from unstd.ids import uid


# (namespace, name, expected) — every answer authored, never recomputed.
_VECTORS = [
    # RFC 4122 §appendix B's own published version-5 vector: namespace DNS,
    # "python.org". Checks the algorithm against the spec, not against itself.
    (
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "python.org",
        "886313e1-3b8a-5372-9b90-0c9aee199e5d",
    ),
    # tools/email/inbox/gmail.py — every Gmail push notification re-derives the
    # conversation from the provider's thread id.
    (
        "a7c3e91b-44d2-4f8a-bf6e-2c9d1e3f5a7b",
        "18c4f9a2b3d5e6f7",
        "7d200fbe-4b29-5431-b467-2fc32ddf216b",
    ),
    # The Go mailbox correspondence namespace, keyed the way Go composes it
    # (provider NUL account id NUL provider thread id). Cross-language parity.
    (
        "6f1d9c30-4a7e-5b81-9c2f-7e0d3a51b8c4",
        "google\x001\x00t1",
        "2b713c5d-1447-522a-8200-a6580df467f4",
    ),
    (
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "",
        "4ebd0208-8328-5d69-8c44-ec50939c0967",
    ),
]


@pytest.mark.parametrize(("namespace", "name", "expected"), _VECTORS)
def test_derive_vectors(namespace: str, name: str, expected: str) -> None:
    assert str(uid.derive(uid.parse(namespace), name)) == expected


def test_derive_is_deterministic_and_v5() -> None:
    ns = uid.parse("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    assert uid.derive(ns, "x") == uid.derive(ns, "x")
    assert uid.derive(ns, "x").version == 5


def test_derive_distinguishes() -> None:
    """The two ways a name-based scheme collapses: shared namespace, shared name.

    Either collision would silently join unrelated entities onto one identity
    row, and the id would look entirely legitimate while it happened.
    """
    one = uid.parse("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    two = uid.parse("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
    assert uid.derive(one, "x") != uid.derive(one, "y")
    assert uid.derive(one, "x") != uid.derive(two, "x")


def test_parse_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="badly formed"):
        uid.parse("not-a-uuid")


def test_new_is_v7_and_unique() -> None:
    ids = [uid.new() for _ in range(100)]
    assert len(set(ids)) == 100
    assert all(i[14] == "7" for i in ids)
    assert sorted(ids) == ids  # v7's whole point: minting order is sort order


def test_new_hex_is_the_same_id_unhyphenated() -> None:
    h = uid.new_hex()
    assert len(h) == 32
    assert "-" not in h


# ── stdlib fallback internals: monotonic counter (RFC 9562 §6.2 Method 1) ──────
#
# `_stdlib_uuid7` is always defined (regardless of whether `uuid-utils` backs
# `new()`/`new_hex()` in this environment), so its monotonicity guarantee is
# testable directly rather than only via a CI leg with the `ids` extra removed.


def test_stdlib_fallback_is_v7_and_unique() -> None:
    ids = [uid._stdlib_uuid7() for _ in range(200)]
    assert len({i.int for i in ids}) == 200
    assert all(i.version == 7 for i in ids)
    assert all(i.variant == RFC_4122 for i in ids)


def test_stdlib_fallback_sorts_in_mint_order() -> None:
    """The whole point of v7: minting order is sort order — including bursts
    minted inside a single millisecond, which is exactly what a pure-random
    rand_a (no counter) cannot guarantee.
    """
    ids = [str(uid._stdlib_uuid7()) for _ in range(500)]
    assert sorted(ids) == ids


def test_stdlib_fallback_counter_increments_within_same_millisecond() -> None:
    """Two mints pinned to the identical millisecond must carry strictly
    increasing counters (bits 75..64), not independent random draws.
    """
    with uid._counter_lock:
        uid._last_ms = None  # force a clean reseed for a deterministic start
        uid._last_counter = 0

    ms = 1_700_000_000_000
    _, c1 = uid._next_ms_and_counter(ms)
    _, c2 = uid._next_ms_and_counter(ms)
    _, c3 = uid._next_ms_and_counter(ms)
    assert c2 == c1 + 1
    assert c3 == c2 + 1


def test_stdlib_fallback_counter_overflow_advances_the_clock() -> None:
    """A 12-bit counter (0xFFF max) must roll the timestamp forward by one
    millisecond rather than wrap back to 0 and silently break ordering.
    """
    with uid._counter_lock:
        uid._last_ms = 1_700_000_000_000
        uid._last_counter = 0xFFF  # already saturated

    next_ms, counter = uid._next_ms_and_counter(1_700_000_000_000)
    assert next_ms == 1_700_000_000_001  # clock forced forward
    assert 0 <= counter <= 0xFFF  # freshly reseeded, back in range


def test_stdlib_fallback_counter_reseeds_on_new_millisecond() -> None:
    with uid._counter_lock:
        uid._last_ms = 1_700_000_000_000
        uid._last_counter = 5

    next_ms, counter = uid._next_ms_and_counter(1_700_000_000_001)
    assert next_ms == 1_700_000_000_001
    assert 0 <= counter <= 0xFFF


def test_stdlib_fallback_holds_the_line_on_backward_clock_step() -> None:
    """A backward wall-clock step (NTP correction) must never re-mint a
    timestamp older than the last one actually issued.
    """
    with uid._counter_lock:
        uid._last_ms = 1_700_000_000_500
        uid._last_counter = 3

    next_ms, counter = uid._next_ms_and_counter(1_700_000_000_000)  # 500ms in the past
    assert next_ms == 1_700_000_000_500
    assert counter == 4


def test_stdlib_fallback_concurrent_mints_never_collide() -> None:
    """Thread-safety of the module-level counter: many threads minting inside
    the same process must never observe a duplicate id.
    """
    out: list[UUID] = []
    lock = threading.Lock()

    def _mint_many() -> None:
        local = [uid._stdlib_uuid7() for _ in range(200)]
        with lock:
            out.extend(local)

    threads = [threading.Thread(target=_mint_many) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(out) == 1600
    assert len({u.int for u in out}) == 1600  # no collisions under contention
