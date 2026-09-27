"""Randomness — one surface, three intents made explicit (replaces ``random``).

Stdlib ``random`` call sites conflate three fundamentally different needs;
``unstd.rand`` splits them into named paths so the intent is legible at the call
site:

1. **Fast bulk draws** — :func:`floats` / :func:`ints` / :func:`choices`, backed by
   numpy ``Generator(PCG64)`` when the ``rand`` extra is installed, falling back to
   stdlib ``random`` when it is absent (this base install).
2. **Explicit crypto** — :mod:`unstd.rand.crypto`, **always** stdlib ``secrets``
   (a CSPRNG), never numpy and unseedable by design.
3. **One seed entrypoint** — :func:`seed` reseeds the bulk / scalar PRNG on both
   backends so an ML / eval run is reproducible; it deliberately never touches the
   crypto path.

A stdlib-``random``-faithful scalar surface (``random``, ``randint``, ``uniform``,
``choice``, ``shuffle``, ``sample``) is stdlib ``random`` itself, bound to the
shared seeded engine — numpy only ever runs where one call fills an array. Import the seed + scalar / bulk surface directly, and the crypto
path as the module so the security intent stays visible::

    from unstd import rand

    rand.seed(1234)  # reproducible bulk / scalar draws
    xs = rand.floats(10_000)  # ndarray (numpy) or list (stdlib fallback)
    tok = rand.crypto.token_hex()  # unseedable CSPRNG token

Backend & fallback: the ``rand`` extra provides ``numpy`` for the bulk helpers.
Without it they run on stdlib ``random`` too — same API, returning ``list``
instead of ``ndarray``. The crypto path never depends on
the extra.
"""

from __future__ import annotations

from unstd.rand import crypto
from unstd.rand.draw import (
    HAVE_NUMPY,
    choice,
    choices,
    floats,
    ints,
    randint,
    random,
    sample,
    seed,
    shuffle,
    uniform,
)


__all__ = [
    "HAVE_NUMPY",
    "choice",
    "choices",
    "crypto",
    "floats",
    "ints",
    "randint",
    "random",
    "sample",
    "seed",
    "shuffle",
    "uniform",
]
