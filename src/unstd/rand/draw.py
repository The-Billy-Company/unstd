r"""Seedable pseudo-random draws — one PRNG surface, numpy-fast with a stdlib twin.

Two backends sit behind one surface:

- **numpy** ``Generator(PCG64)`` when the ``rand`` extra is installed — vectorized
  bulk draws (arrays of floats / ints / choices) at C speed.
- stdlib :mod:`random` (Mersenne Twister) otherwise — same surface, scalar speed;
  the bulk helpers materialize Python ``list``\\ s instead of ndarrays.

:func:`seed` is the single reproducibility entrypoint: it reseeds **both** backends
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

# Module-global engines. ``_py`` is both the stdlib fallback and the reproducibility
# twin (always reseeded); ``_gen`` is the numpy Generator(PCG64) when present.
_py = _random.Random()  # noqa: S311 — deliberate non-crypto PRNG; the CSPRNG lives in `crypto`
# Invariant: ``_gen is None`` exactly when numpy is absent — the draw functions
# below branch on that (it narrows for the type checker where the bool can't).
_gen = _np.random.Generator(_np.random.PCG64()) if HAVE_NUMPY else None


def seed(n: int | None = None) -> None:
    """Seed the PRNG for reproducible draws on the active backend.

    Reseeds both the numpy ``Generator(PCG64)`` and the stdlib fallback, so a run is
    reproducible whichever backend is active. Does **not** touch
    :mod:`unstd.rand.crypto` — the CSPRNG is unseedable by design.
    """
    global _gen
    _py.seed(n)
    if HAVE_NUMPY:
        _gen = _np.random.Generator(_np.random.PCG64(n))


def _below(n: int) -> int:
    """Return a random index in ``[0, n)`` on the active backend."""
    return _py.randrange(n) if _gen is None else int(_gen.integers(n))


def random() -> float:
    """Return a float in ``[0.0, 1.0)`` (stdlib ``random.random`` twin)."""
    return _py.random() if _gen is None else float(_gen.random())


def randint(a: int, b: int) -> int:
    """Return a random int in ``[a, b]`` — **inclusive** both ends (stdlib semantics)."""
    return (
        _py.randint(a, b) if _gen is None else int(_gen.integers(a, b, endpoint=True))
    )


def uniform(a: float, b: float) -> float:
    """Return a random float in ``[a, b)`` (stdlib ``random.uniform`` twin)."""
    return _py.uniform(a, b) if _gen is None else float(_gen.uniform(a, b))


def choice[T](seq: Sequence[T]) -> T:
    """Return a single uniformly-random element of *seq* (the original Python object)."""
    return seq[_below(len(seq))]


def shuffle[T](x: list[T]) -> None:
    """Shuffle list *x* in place (Fisher-Yates over the active backend's PRNG).

    Index-based on the numpy backend so Python objects are permuted in place rather
    than coerced through an ndarray — identical semantics to ``random.shuffle``.
    """
    if _gen is None:
        _py.shuffle(x)
        return
    for i in range(len(x) - 1, 0, -1):
        j = int(_gen.integers(i + 1))
        x[i], x[j] = x[j], x[i]


def sample[T](population: Sequence[T], k: int) -> list[T]:
    """*k* distinct elements sampled **without** replacement (original objects)."""
    if _gen is None:
        return _py.sample(list(population), k)
    idx = _gen.choice(len(population), size=k, replace=False)
    return [population[int(i)] for i in idx]


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
