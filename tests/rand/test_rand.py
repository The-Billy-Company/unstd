r"""Adversarial + parity tests for ``unstd.rand``.

These pin the three intents the module makes explicit:

- the **crypto** path rides stdlib ``secrets`` — independent of :func:`rand.seed`
  (unseedable by design), so seeding can never make a token reproducible;
- :func:`rand.seed` makes bulk + scalar draws reproducible on the **active**
  backend (numpy ``Generator(PCG64)`` or the stdlib ``random`` fallback);
- the stdlib fallback is what runs in this base install (no ``rand`` extra) — the
  bulk helpers return Python ``list``\\ s, and every draw stays in bounds.

Assertions are deliberately strict — never weakened to go green.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from unstd import rand
from unstd.rand import crypto, draw


if TYPE_CHECKING:
    from collections.abc import Iterable


def _floats(x: Iterable[float]) -> list[float]:
    """Normalize a bulk result (ndarray | list) to a Python ``list[float]``."""
    return [float(v) for v in x]


# ── the crypto path: stdlib secrets, unseedable, independent of seed() ─────────


def test_crypto_is_independent_of_seed() -> None:
    # Seeding the PRNG must NOT make the CSPRNG reproducible — the whole point of a
    # separate crypto path. 32 bytes of collision would be ~2^-256; a repeat here
    # would mean the crypto path leaked into the seeded engine.
    """Test crypto is independent of seed."""
    rand.seed(1234)
    a = crypto.token_bytes(32)
    rand.seed(1234)
    b = crypto.token_bytes(32)
    assert a != b


def test_crypto_tokens_are_unique_across_many_draws() -> None:
    """Test crypto tokens are unique across many draws."""
    tokens = {crypto.token_hex(16) for _ in range(2000)}
    assert len(tokens) == 2000  # a CSPRNG never repeats over this horizon


def test_crypto_bounds_and_shapes() -> None:
    """Test crypto bounds and shapes."""
    assert len(crypto.token_bytes(16)) == 16
    assert isinstance(crypto.token_bytes(1), bytes)
    assert len(crypto.token_hex(8)) == 16  # 2 hex chars per byte
    assert all(crypto.token_urlsafe(24))  # non-empty urlsafe token
    for _ in range(500):
        assert 0 <= crypto.below(97) < 97  # [0, n), no modulo bias
    assert crypto.bits(0) == 0
    assert 0 <= crypto.bits(16) < (1 << 16)
    seq = ("a", "b", "c", "d")
    assert crypto.choice(seq) in seq


# ── seed(): bulk + scalar draws reproducible on the active backend ─────────────


def test_seed_makes_bulk_floats_reproducible() -> None:
    """Test seed makes bulk floats reproducible."""
    rand.seed(42)
    a = _floats(rand.floats(256))
    rand.seed(42)
    b = _floats(rand.floats(256))
    assert a == b


def test_seed_makes_bulk_ints_reproducible_and_scalars_too() -> None:
    """Test seed makes bulk ints reproducible and scalars too."""
    rand.seed(7)
    ints_a = [int(v) for v in rand.ints(128, 0, 1000)]
    scal_a = [rand.randint(1, 6) for _ in range(64)]
    rand.seed(7)
    ints_b = [int(v) for v in rand.ints(128, 0, 1000)]
    scal_b = [rand.randint(1, 6) for _ in range(64)]
    assert ints_a == ints_b
    assert scal_a == scal_b


def test_seed_makes_choices_and_shuffle_reproducible() -> None:
    """Test seed makes choices and shuffle reproducible."""
    pool = list(range(50))
    rand.seed(99)
    ch_a = rand.choices(pool, 40, weights=[float(i + 1) for i in pool])
    sh_a = pool.copy()
    rand.shuffle(sh_a)
    rand.seed(99)
    ch_b = rand.choices(pool, 40, weights=[float(i + 1) for i in pool])
    sh_b = pool.copy()
    rand.shuffle(sh_b)
    assert ch_a == ch_b
    assert sh_a == sh_b


def test_distinct_seeds_diverge() -> None:
    """Test distinct seeds diverge."""
    rand.seed(1)
    a = _floats(rand.floats(64))
    rand.seed(2)
    b = _floats(rand.floats(64))
    assert a != b


def test_seed_none_does_not_crash_and_stays_random() -> None:
    """Test seed none does not crash and stays random."""
    rand.seed(None)
    a = _floats(rand.floats(32))
    rand.seed(None)
    b = _floats(rand.floats(32))
    assert a != b  # entropy-seeded, so two runs differ


# ── the container divergence between the two backends ─────────────────────────


@pytest.fixture
def stdlib_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the stdlib-``random`` fallback regardless of whether numpy is present.

    The bulk-container divergence is a property of the fallback *code path*, not of
    the ambient environment — a shared workspace venv often pulls numpy in
    transitively, so the fallback would otherwise never be exercised here. Flipping
    the backend flag both call sites read makes the fallback deterministic under test.
    """
    monkeypatch.setattr(draw, "HAVE_NUMPY", False)
    monkeypatch.setattr(rand, "HAVE_NUMPY", False)
    # Keep the module invariant: ``_gen is None`` exactly when numpy is absent.
    # Bulk helpers branch on ``_gen``, not the flag alone.
    monkeypatch.setattr(draw, "_gen", None)


def test_reexport_shares_the_backend_flag() -> None:
    # `rand` re-exports draw's surface, so a caller reading `rand.HAVE_NUMPY` must
    # see exactly the backend `draw` selected at import — one live decision.
    """Test reexport shares the backend flag."""
    assert rand.HAVE_NUMPY is draw.HAVE_NUMPY


@pytest.mark.usefixtures("stdlib_backend")
def test_fallback_bulk_helpers_return_python_lists() -> None:
    # The pinned divergence: on the stdlib backend, bulk draws are plain lists.
    """Test fallback bulk helpers return python lists."""
    assert isinstance(rand.floats(8), list)
    assert isinstance(rand.ints(8, 0, 10), list)
    assert isinstance(rand.choices(["x", "y"], 8), list)


@pytest.mark.skipif(
    not draw.HAVE_NUMPY, reason="numpy backend not installed (rand extra absent)"
)
def test_numpy_backend_bulk_helpers_return_ndarrays() -> None:
    # The other half of the divergence: with numpy live, bulk draws are ndarrays so
    # callers keep the vectorized array. `choices` returns a list on both backends.
    """Test numpy backend bulk helpers return ndarrays."""
    import numpy as np

    assert isinstance(rand.floats(8), np.ndarray)
    assert isinstance(rand.ints(8, 0, 10), np.ndarray)
    assert isinstance(rand.choices(["x", "y"], 8), list)


# ── distribution sanity: bounds, lengths, membership ──────────────────────────


def test_scalar_bounds() -> None:
    """Test scalar bounds."""
    rand.seed(0)
    for _ in range(1000):
        assert 0.0 <= rand.random() < 1.0
        assert 1 <= rand.randint(1, 6) <= 6  # inclusive both ends
        assert -2.0 <= rand.uniform(-2.0, 5.0) < 5.0


def test_bulk_lengths_and_bounds() -> None:
    """Test bulk lengths and bounds."""
    rand.seed(0)
    fs = _floats(rand.floats(300, low=2.0, high=3.0))
    assert len(fs) == 300
    assert all(2.0 <= v < 3.0 for v in fs)

    ks = [int(v) for v in rand.ints(300, 10, 20)]
    assert len(ks) == 300
    assert all(10 <= v < 20 for v in ks)  # [low, high)

    ks_inc = [int(v) for v in rand.ints(300, 10, 20, endpoint=True)]
    assert all(10 <= v <= 20 for v in ks_inc)  # [low, high]


def test_choice_and_sample_membership() -> None:
    """Test choice and sample membership."""
    pool = list(range(20))
    rand.seed(3)
    for _ in range(200):
        assert rand.choice(pool) in pool

    picked = rand.sample(pool, 8)
    assert len(picked) == 8
    assert len(set(picked)) == 8  # distinct — sampled without replacement
    assert set(picked) <= set(pool)

    bulk = rand.choices(pool, 100)
    assert len(bulk) == 100
    assert set(bulk) <= set(pool)


def test_shuffle_is_a_permutation() -> None:
    """Test shuffle is a permutation."""
    original = list(range(100))
    shuffled = original.copy()
    rand.seed(5)
    rand.shuffle(shuffled)
    assert sorted(shuffled) == original  # same multiset — nothing lost/added
    assert len(shuffled) == len(original)


def test_weighted_choices_respects_bias() -> None:
    # A heavily-skewed weight vector must dominate the empirical distribution — a
    # real bias check, not just "runs without error".
    """Test weighted choices respects bias."""
    rand.seed(11)
    picks = rand.choices(["rare", "common"], 4000, weights=[1.0, 99.0])
    assert picks.count("common") > picks.count("rare") * 10


def test_sample_full_population_is_a_permutation() -> None:
    """Test sample full population is a permutation."""
    pool = list(range(30))
    rand.seed(13)
    full = rand.sample(pool, len(pool))
    assert sorted(full) == pool
