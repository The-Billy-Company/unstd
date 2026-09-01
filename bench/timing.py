"""The measurement primitive: one number per callable, as quietly as possible.

Microbenchmarks on a shared laptop are mostly noise, and the noise is one-sided —
a scheduler preemption, a page fault, or a turbo downclock can only ever make a
sample *slower*. So the estimator here is the **minimum** of several batch means,
not the average: the fastest batch observed is the one least contaminated by work
that was not the code under test. That is the same reason ``timeit`` documents
``min()`` over its repeats rather than the mean.

Batch size is calibrated per callable rather than fixed, because the callables
here span four orders of magnitude (a 0.9 µs dict clone and a 2 ms JSON encode
cannot share an iteration count without either drowning in loop overhead or
taking a minute). Each batch is grown until it runs long enough that the
``perf_counter_ns`` resolution and the loop itself are rounding error.
"""

from __future__ import annotations

from dataclasses import dataclass
import gc
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


# A batch must run at least this long for the loop overhead and the clock's own
# resolution to be negligible against it. 5 ms is ~5 000× a typical
# `perf_counter_ns` tick and ~50 000× the per-iteration loop cost.
_MIN_BATCH_NS = 5_000_000
# Ceiling on a single batch, so a pathologically slow case cannot hang a run.
_MAX_BATCH_NS = 250_000_000
_MAX_ITERS = 1 << 24


@dataclass(frozen=True, slots=True)
class Measurement:
    """What one callable cost, and how hard we looked."""

    per_op_ns: float
    iters: int
    rounds: int
    spread: float
    """Relative gap between the fastest and slowest batch — the noise floor of
    this run. Above ~0.25 the machine was busy and the number is soft."""

    @property
    def per_op_us(self) -> float:
        return self.per_op_ns / 1_000.0

    def human(self) -> str:
        """Render the cost in whichever unit keeps it legible."""
        ns = self.per_op_ns
        if ns < 1_000:
            return f"{ns:.0f} ns"
        if ns < 1_000_000:
            return f"{ns / 1_000:.1f} µs"
        return f"{ns / 1_000_000:.2f} ms"


def _batch(fn: Callable[[], Any], iters: int) -> int:
    """Run ``fn`` ``iters`` times and return the elapsed nanoseconds.

    The loop is deliberately dumb — no enumerate, no closure lookups beyond the
    two locals — so that what is measured is as close to ``fn`` as Python allows.
    """
    start = time.perf_counter_ns()
    for _ in range(iters):
        fn()
    return time.perf_counter_ns() - start


def _calibrate(fn: Callable[[], Any]) -> tuple[int, int]:
    """Grow a batch until it runs long enough to be worth timing."""
    iters = 1
    while iters < _MAX_ITERS:
        elapsed = _batch(fn, iters)
        if elapsed >= _MIN_BATCH_NS:
            return iters, elapsed
        # Project the iteration count that would hit the target, then double it
        # for headroom, so calibration converges in a couple of steps instead of
        # walking a power-of-two ladder from 1.
        scale = max(2, int(_MIN_BATCH_NS / max(elapsed, 1)))
        iters = min(iters * min(scale, 64), _MAX_ITERS)
    return iters, _batch(fn, iters)


def measure(fn: Callable[[], Any], *, rounds: int = 5) -> Measurement:
    """Time ``fn`` and return the least-contaminated per-call cost.

    Garbage collection is disabled for the duration: a collection triggered
    inside the timed region is charged to whichever callable happened to cross
    the allocation threshold, which for a clone benchmark is pure coin-flip. It
    is restored on the way out even if ``fn`` raises.
    """
    fn()  # warm caches, JIT-ish specialization, and any lazy import inside fn
    gc_was_on = gc.isenabled()
    gc.disable()
    try:
        iters, first = _calibrate(fn)
        if iters * (first / iters) > _MAX_BATCH_NS:
            rounds = min(rounds, 2)
        samples = [first, *(_batch(fn, iters) for _ in range(rounds - 1))]
    finally:
        if gc_was_on:
            gc.enable()
        gc.collect()

    best, worst = min(samples), max(samples)
    return Measurement(
        per_op_ns=best / iters,
        iters=iters,
        rounds=len(samples),
        spread=(worst - best) / best if best else 0.0,
    )
