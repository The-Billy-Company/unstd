"""Stateless integer batches using the explicit SplitMix64-v1 stream.

Each unsigned 64-bit seed starts an independent local SplitMix64 state. A draw
adds the golden-ratio increment, applies Stafford's finalizer, and uses modulo
rejection to avoid range bias. Rejected words advance only that seed's local
state. Results therefore survive reordering, splitting, and concatenating a
batch without changing any ordinary scalar or bulk generator state.

This is a simulation stream, never a cryptographic primitive. It deliberately
does not reproduce ``random.Random(seed).randint(...)``.
"""

from __future__ import annotations

from operator import index
from typing import TYPE_CHECKING


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_native_seeded_ints: Callable[[list[int], int, int], list[int]] | None
try:
    from unstd.rand import _rand_native
except ImportError:
    _native_seeded_ints = None
else:
    # Source checkouts and older installed kernels can have shuffle alone.
    _native_seeded_ints = getattr(_rand_native, "seeded_ints", None)


_LIMIT = 1 << 64
_MASK = _LIMIT - 1
_MIN_INT64 = -(1 << 63)
_MAX_INT64 = (1 << 63) - 1


def _python_seeded_ints(seeds: list[int], low: int, width: int) -> list[int]:
    """Draw only after every seed has passed the unsigned 64-bit admission."""
    validated = []
    for item in seeds:
        value = index(item)
        if not 0 <= value < _LIMIT:
            msg = "seeds must contain unsigned 64-bit integers"
            raise ValueError(msg)
        validated.append(value)

    threshold = _LIMIT % width
    result = []
    for value in validated:
        while True:
            value = (value + 0x9E3779B97F4A7C15) & _MASK
            word = value
            word = ((word ^ (word >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
            word = ((word ^ (word >> 27)) * 0x94D049BB133111EB) & _MASK
            word ^= word >> 31
            if word >= threshold:
                result.append(low + word % width)
                break
    return result


def seeded_ints(
    n: int, low: int, high: int, endpoint: bool, seeds: Sequence[int]
) -> list[int]:
    """Validate a complete independent-seed batch, then choose its local kernel."""
    n = index(n)
    if n < 0:
        msg = "n must be non-negative"
        raise ValueError(msg)
    low, high = index(low), index(high)
    top = high if endpoint else high - 1
    if not (_MIN_INT64 <= low <= _MAX_INT64 and _MIN_INT64 <= top <= _MAX_INT64):
        msg = "seeded integer bounds must fit signed int64"
        raise ValueError(msg)
    width = top - low + 1
    if not 0 < width <= _LIMIT:
        msg = "integer interval must contain at least one value"
        raise ValueError(msg)
    snapshot = list(seeds)
    if len(snapshot) != n:
        msg = "n must match the number of seeds"
        raise ValueError(msg)
    if _native_seeded_ints is not None:
        return _native_seeded_ints(snapshot, low, width)
    return _python_seeded_ints(snapshot, low, width)
