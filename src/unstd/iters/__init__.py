"""Lazy iterator & functional combinators the stdlib is missing.

``itertools`` and ``functools`` cover a lot, but the recurring
accumulate-into-a-list idioms — chunk this, slide a window over that, take the
first N, drop duplicates while preserving order, split on a predicate in one
pass — are left as copy-paste "recipes". This module implements that curated set
**natively on top of the stdlib** so those hand-rolled accumulator loops become
one named call.

Unlike the other ``unstd`` groups, ``iters`` is **PURE STDLIB — no optional
extra, no third-party backend.** It is additive combinators (a curated,
``more-itertools``-style set) built directly on ``itertools``/``functools``, so
it is always available in a base ``unstd`` install with zero dependency weight.

Conventions:

- Everything returns a **lazy iterator** unless the name implies materialization
  (``take`` → ``list``).
- Nothing re-implements a primitive stdlib already provides identically — only
  the missing combinators, plus two convenience re-exports
  (:func:`itertools.batched`, :func:`itertools.pairwise`) so this is the single
  import home for "iterate over a sequence in a shape".

Prior art: the official Python ``itertools`` recipes section and
``more-itertools`` (Bettini et al.,
https://github.com/more-itertools/more-itertools) — the recipes these mirror.
These are **native reimplementations, not a dependency** on ``more-itertools``.

Import the members directly::

    from unstd.iters import chunked, windowed, first, unique_everseen, partition
"""

from __future__ import annotations

from itertools import batched, pairwise

from unstd.iters.consecutive import chunked, runs, windowed
from unstd.iters.distinct import unique_everseen, unique_justseen
from unstd.iters.reshape import flatten, partition
from unstd.iters.select import first, ilen, last, nth, take


__all__ = [
    "batched",
    "chunked",
    "first",
    "flatten",
    "ilen",
    "last",
    "nth",
    "pairwise",
    "partition",
    "runs",
    "take",
    "unique_everseen",
    "unique_justseen",
    "windowed",
]
