"""Stateful UUIDv7 clock histories for the stdlib fallback (RFC 9562 §6.2 Method 1).

The machine drives the public ``new``/``new_hex`` through the fallback under an
injected millisecond clock (advance, stall, rollback, same-millisecond bursts)
and injected entropy (counter seeds near 0xFFF force overflow; failing draws
raise mid-mint).

The model treats ``unix_ts_ms`` and the 12-bit counter as one 60-bit key. A clock
ahead of the last key's millisecond starts a fresh key from the seed. Otherwise
the key increments by one, and a carry out of the counter moves the millisecond
on and reseeds. Each minted UUID is decoded with stdlib ``uuid.UUID`` and must
equal the model's exact 128 bits. A failed seed draw publishes nothing. A failed
tail draw has already reserved its key, which the README's reservation-order
contract implies.
"""

from __future__ import annotations

import errno
from itertools import pairwise
from unittest.mock import patch
import uuid

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
import pytest

from unstd.ids import uid


_MS_MAX = (1 << 48) - 1
_COUNTER = 0xFFF
_TAIL = (1 << 62) - 1

seeds = st.one_of(
    st.sampled_from([0xFFF, 0xFFE, 0xFFFF, 0xF000, 0]), st.integers(0, 0xFFFF)
)
tails = st.integers(0, (1 << 64) - 1)


class Uuid7Clock(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.saved = uid._last_ms, uid._last_counter
        with uid._counter_lock:
            uid._last_ms, uid._last_counter = None, 0
        self.now = 0
        self.key: int | None = None
        self.issued: list[int] = []

    def teardown(self) -> None:
        with uid._counter_lock:
            uid._last_ms, uid._last_counter = self.saved

    @initialize(start=st.integers(0, _MS_MAX - 10**7))
    def start(self, start: int) -> None:
        self.now = start

    # ── clock ────────────────────────────────────────────────────────────────

    @rule(delta=st.integers(1, 10**6))
    def advance(self, delta: int) -> None:
        self.now = min(self.now + delta, _MS_MAX - 10**6)

    @rule(delta=st.integers(1, 10**6))
    def rollback(self, delta: int) -> None:
        self.now = max(self.now - delta, 0)

    # ── model ────────────────────────────────────────────────────────────────

    def _next(self, seed: int) -> tuple[int, bool]:
        if self.key is None or self.now > self.key >> 12:
            return self.now << 12 | seed & _COUNTER, True
        key = self.key + 1
        carried = not key & _COUNTER
        return (key | seed & _COUNTER if carried else key), carried

    def _mint(
        self, api: str, seed: int, tail: int, fail: int | None = None
    ) -> tuple[str | None, list[int]]:
        requests: list[int] = []
        error = OSError(errno.EIO, "injected entropy failure")

        def entropy(size: int) -> bytes:
            requests.append(size)
            if size == fail:
                raise error
            return (seed if size == 2 else tail).to_bytes(size)

        with (
            patch.object(uid, "_uuid7", uid._stdlib_uuid7),
            patch.object(uid.time, "time_ns", lambda: self.now * 1_000_000 + 999_999),
            patch.object(uid.os, "urandom", entropy),
        ):
            if fail is None:
                return getattr(uid, api)(), requests
            with pytest.raises(OSError, match="injected entropy failure") as caught:
                getattr(uid, api)()
        assert caught.value is error
        assert not uid._counter_lock.locked()
        return None, requests

    def _issue(self, api: str, seed: int, tail: int) -> None:
        key, reseeded = self._next(seed)
        raw, requests = self._mint(api, seed, tail)
        assert raw is not None
        parsed = uuid.UUID(raw)
        assert raw == (parsed.hex if api == "new_hex" else str(parsed))
        assert parsed.version == 7
        assert parsed.variant == uuid.RFC_4122
        assert parsed.int >> 80 == key >> 12
        assert parsed.int >> 64 & _COUNTER == key & _COUNTER
        assert parsed.int & _TAIL == tail & _TAIL
        assert (
            parsed.int
            == (key >> 12) << 80
            | 7 << 76
            | (key & _COUNTER) << 64
            | 2 << 62
            | tail & _TAIL
        )
        assert requests == ([2, 8] if reseeded else [8])
        self.key = key
        self.issued.append(parsed.int)

    # ── mints ────────────────────────────────────────────────────────────────

    @rule(api=st.sampled_from(["new", "new_hex"]), seed=seeds, tail=tails)
    def mint(self, api: str, seed: int, tail: int) -> None:
        self._issue(api, seed, tail)

    @rule(count=st.integers(2, 40), seed=seeds, tail=tails)
    def burst(self, count: int, seed: int, tail: int) -> None:
        for _ in range(count):
            self._issue("new", seed, tail)

    @rule(seed=seeds)
    def seed_failure(self, seed: int) -> None:
        _, reseeds = self._next(seed)
        if not reseeds:
            return
        raw, requests = self._mint("new", seed, 0, fail=2)
        assert (raw, requests) == (None, [2])

    @rule(seed=seeds)
    def tail_failure(self, seed: int) -> None:
        key, reseeded = self._next(seed)
        raw, requests = self._mint("new", seed, 0, fail=8)
        assert (raw, requests) == (None, [2, 8] if reseeded else [8])
        self.key = key

    @invariant()
    def strictly_ordered(self) -> None:
        assert all(a < b for a, b in pairwise(self.issued))


test_uuid7_clock_histories = Uuid7Clock.TestCase
