r"""Seedable pseudo-random draws — one PRNG surface, numpy-fast with a stdlib twin.

Each draw rides the engine that is fastest *for its shape*:

- **Scalar draws** (:data:`random`, :data:`randint`, :data:`uniform`,
  :data:`choice`, :data:`shuffle`, :data:`sample`) are stdlib :mod:`random`'s
  Mersenne Twister, bound directly — on every install. A numpy ``Generator``
  call pays per-call dispatch that costs 5-7x what the whole stdlib draw does,
  so there is nothing to accelerate one value at a time.
- **Bulk draws** (:func:`floats`, :func:`ints`, :func:`choices`) fill a whole
  array per call on numpy ``Generator(PCG64)`` when the ``rand`` extra is
  installed, and fall back to stdlib loops returning ``list`` without it.

:func:`seed` is the single reproducibility entrypoint: it reseeds **both** engines
so an ML / eval run is deterministic regardless of which one is active. This surface
is for simulation / sampling / shuffling — never secrets. Use
:mod:`unstd.rand.crypto` (stdlib ``secrets``, unseedable by design) for anything
security-sensitive.

Divergence (deliberate, pinned as a regression guard in ``tests/rand/test_rand.py``):
the bulk helpers (:func:`floats`, :func:`ints`) return a numpy ``ndarray`` on the
numpy backend and a Python ``list`` on the stdlib fallback — same values, different
container — so a caller keeps the vectorized array where it exists.

Prior art: NumPy ``Generator`` / ``PCG64`` — Melissa O'Neill, "PCG: A Family of
Simple Fast Space-Efficient Statistically Good Algorithms for Random Number
Generation" (2014). stdlib ``random`` — the Mersenne Twister (Matsumoto &
Nishimura, 1998).
"""

from __future__ import annotations

import random as _random
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as _np
    from numpy.typing import NDArray

    HAVE_NUMPY = True

    FloatArray = NDArray[_np.float64] | list[float]
    IntArray = NDArray[_np.int64] | list[int]
else:
    try:
        import numpy as _np

        HAVE_NUMPY = True
    except (
        ImportError
    ):  # base install without the `rand` extra — stdlib random fallback
        _np = None
        HAVE_NUMPY = False


__all__ = [
    "HAVE_NUMPY",
    "choice",
    "choices",
    "floats",
    "ints",
    "randint",
    "random",
    "sample",
    "seed",
    "shuffle",
    "uniform",
]

# Module-global engines. ``_py`` owns every scalar draw on both backends — a
# numpy ``Generator`` call pays ~150 ns of dispatch per value, 5-7x the stdlib
# Mersenne Twister's, so routing one-at-a-time draws through it was a pure loss.
# ``_gen`` is the numpy Generator(PCG64), used only where a call fills an array.
_py = _random.Random()  # noqa: S311 — deliberate non-crypto PRNG; the CSPRNG lives in `crypto`
# Invariant: ``_gen is None`` exactly when numpy is absent — the bulk draws below
# branch on that (it narrows for the type checker where the bool can't).
_gen = _np.random.Generator(_np.random.PCG64()) if HAVE_NUMPY else None


def seed(n: int | None = None) -> None:
    """Seed both engines, so scalar and bulk draws are reproducible on either backend.

    Does **not** touch :mod:`unstd.rand.crypto` — the CSPRNG is unseedable by design.
    """
    global _gen
    _py.seed(n)
    if HAVE_NUMPY:
        _gen = _np.random.Generator(_np.random.PCG64(n))


# ── scalar surface — stdlib ``random`` twins, bound to the shared seeded engine ──

random = _py.random
"""A float in ``[0.0, 1.0)``."""

randint = _py.randint
"""A random int in ``[a, b]`` — **inclusive** both ends."""

uniform = _py.uniform
"""A random float in ``[a, b]``."""

choice = _py.choice
"""A single uniformly-random element of a non-empty sequence."""

shuffle = _py.shuffle
"""Shuffle a list in place."""

sample = _py.sample
"""*k* distinct elements sampled **without** replacement."""


# ── bulk surface — one call fills an array ─────────────────────────────────────


def floats(n: int, *, low: float = 0.0, high: float = 1.0) -> FloatArray:
    """*n* floats in ``[low, high)`` — an ndarray on numpy, a ``list`` on stdlib."""
    if _gen is not None:
        return _gen.uniform(low, high, size=n)
    return [_py.uniform(low, high) for _ in range(n)]


def ints(n: int, low: int, high: int, *, endpoint: bool = False) -> IntArray:
    """*n* ints in ``[low, high)`` (or ``[low, high]`` when *endpoint*).

    An ndarray on the numpy backend, a ``list`` on the stdlib fallback.
    """
    if _gen is not None:
        return _gen.integers(low, high, size=n, endpoint=endpoint)
    hi = high if endpoint else high - 1
    return [_py.randint(low, hi) for _ in range(n)]


def choices[T](
    seq: Sequence[T], n: int, *, weights: Sequence[float] | None = None
) -> list[T]:
    """*n* elements sampled **with** replacement (optionally *weights*-biased)."""
    items = list(seq)
    if _gen is None:
        return _py.choices(items, weights=weights, k=n)
    p = None
    if weights is not None:
        w = _np.asarray(weights, dtype=float)
        p = w / w.sum()
    idx = _gen.choice(len(items), size=n, replace=True, p=p)
    return [items[int(i)] for i in idx]
