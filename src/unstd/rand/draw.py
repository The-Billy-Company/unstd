r"""Seedable pseudo-random draws with independent, explicitly selected streams.

Each draw rides the engine that is fastest *for its shape*:

- **Scalar draws** (:data:`random`, :data:`randint`, :data:`uniform`,
  :data:`choice`, :data:`shuffle`, :data:`sample`) are stdlib :mod:`random`'s
  Mersenne Twister, bound directly — on every install. A numpy ``Generator``
  call pays per-call dispatch that costs 5-7x what the whole stdlib draw does,
  so there is nothing to accelerate one value at a time.
- **Bulk draws** (:func:`floats`, :func:`ints`, :func:`choices`) fill a whole
  array per call on numpy ``Generator(PCG64)`` when the ``rand`` extra is
  installed, and fall back to stdlib loops returning ``list`` without it.

:func:`seed` reseeds **both** module engines. Repeating a seed and call sequence
reproduces draws with the same backend; changing backends changes bulk values
and which draws share state. :class:`Generator` creates an independent stream,
defaulting to stdlib even when NumPy is installed, with explicit NumPy opt-in.
This surface is for simulation / sampling / shuffling — never secrets. Use
:mod:`unstd.rand.crypto` (stdlib ``secrets``, unseedable by design) for anything
security-sensitive.

Divergence (deliberate, pinned as a regression guard in the rand tests):
the bulk helpers (:func:`floats`, :func:`ints`) return a numpy ``ndarray`` on the
numpy backend and a Python ``list`` on the stdlib fallback, with different seeded
sequences, so a caller keeps the vectorized array where it exists.

Prior art: NumPy ``Generator`` / ``PCG64`` — Melissa O'Neill, "PCG: A Family of
Simple Fast Space-Efficient Statistically Good Algorithms for Random Number
Generation" (2014). stdlib ``random`` — the Mersenne Twister (Matsumoto &
Nishimura, 1998).
"""

# Early Python 3.12 cannot resolve deferred function type parameters in
# get_type_hints. Evaluate public annotations here; defer private NumPy types.
from collections.abc import MutableSequence, Sequence
from math import isfinite
from operator import index
import random as _random
from typing import TYPE_CHECKING, Literal

from unstd.rand.keyed import seeded_ints as _keyed_ints


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    import numpy as _np
    from numpy.typing import NDArray

    class _NativeShuffle(Protocol):
        def shuffle[T](
            self, getrandbits: Callable[[int], int], items: list[T]
        ) -> None: ...

    _native: _NativeShuffle | None

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

    # Runtime introspection must work without requiring the optional NumPy types.
    FloatArray = _np.ndarray | list[float] if HAVE_NUMPY else list[float]
    IntArray = _np.ndarray | list[int] if HAVE_NUMPY else list[int]


try:
    from unstd.rand import _rand_native as _native
except ImportError:
    _native = None


__all__ = [
    "HAVE_NUMPY",
    "Generator",
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
_py = _random.Random()  # noqa: S311  # nosec B311 # non-crypto PRNG; security draws use `crypto`
# Invariant: ``_gen is None`` exactly when numpy is absent — the bulk draws below
# branch on that (it narrows for the type checker where the bool can't).
_gen = _np.random.Generator(_np.random.PCG64()) if HAVE_NUMPY else None

# Native shuffling keeps Random's live getrandbits rejection stream. A changed
# method or per-instance hook must continue through the stdlib implementation.
_stdlib_shuffle = _random.Random.shuffle
_stdlib_randbelow = _random.Random._randbelow  # type: ignore[attr-defined]
_stdlib_getrandbits = _random.Random.getrandbits
_stdlib_uniform = _random.Random.uniform
_stdlib_random = _random.Random.random
_stdlib_randint = _random.Random.randint
_stdlib_randrange = _random.Random.randrange


def seed(n: int | None = None) -> None:
    """Reseed both module engines without changing either if validation fails.

    Reproducibility requires the same backend and call sequence. Does **not**
    touch :mod:`unstd.rand.crypto` — the CSPRNG is unseedable by design.
    """
    global _gen
    replacement = _np.random.Generator(_np.random.PCG64(n)) if HAVE_NUMPY else None
    _py.seed(n)
    if HAVE_NUMPY:
        _gen = replacement


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


def _size(n: int) -> int:
    n = index(n)
    if n < 0:
        msg = "n must be non-negative"
        raise ValueError(msg)
    return n


def _floats(
    py: _random.Random,
    gen: "_np.random.Generator | None",
    n: int,
    low: float,
    high: float,
) -> FloatArray:
    n = _size(n)
    try:
        finite = isfinite(low) and isfinite(high) and isfinite(float(high) - float(low))
    except OverflowError:
        finite = False
    if not finite:
        msg = "low, high, and their difference must be finite"
        raise ValueError(msg)
    if high < low:
        msg = "high must be greater than or equal to low"
        raise ValueError(msg)
    if gen is not None:
        # Unit uniform uses the same doubles and state as random(), without
        # NumPy's general interval dispatch. Keep numeric hooks on uniform().
        if type(low) is float and low == 0.0 and type(high) is float and high == 1.0:
            return gen.random(n)
        return gen.uniform(low, high, size=n)
    if (
        n > 1
        and type(py) is _random.Random
        and type(low) in (int, float)
        and type(high) in (int, float)
        and _random.Random.uniform is _stdlib_uniform
        and _random.Random.random is _stdlib_random
        and "uniform" not in py.__dict__
        and "random" not in py.__dict__
    ):
        # Match Random.uniform's arithmetic order and draw consumption, while
        # calculating the immutable interval only once for the whole batch.
        span = high - low
        draw = py.random
        return [low + span * draw() for _ in range(n)]
    return [py.uniform(low, high) for _ in range(n)]


def floats(n: int, *, low: float = 0.0, high: float = 1.0) -> FloatArray:
    """*n* floats in ``[low, high)`` — an ndarray on NumPy, a ``list`` on stdlib.

    Rounding can include *high*, as with stdlib ``uniform``. *n* must be a
    non-negative integer and *high* must be at least *low*. Bounds and their
    difference must be finite, including when *n* is zero.
    """
    return _floats(_py, _gen, n, low, high)


def _ints(
    py: _random.Random,
    gen: "_np.random.Generator | None",
    n: int,
    low: int,
    high: int,
    endpoint: bool,
    seeds: Sequence[int] | None = None,
) -> IntArray:
    n = _size(n)
    low, high = index(low), index(high)
    if high < low or (high == low and not endpoint):
        msg = "integer interval must contain at least one value"
        raise ValueError(msg)
    if seeds is not None:
        values = _keyed_ints(n, low, high, endpoint, seeds)
        return _np.asarray(values, dtype=_np.int64) if gen is not None else values
    if gen is not None:
        return gen.integers(low, high, size=n, endpoint=endpoint)
    hi = high if endpoint else high - 1
    if (
        n > 1
        and type(py) is _random.Random
        and _random.Random.randint is _stdlib_randint
        and _random.Random.randrange is _stdlib_randrange
        and "randint" not in py.__dict__
        and "randrange" not in py.__dict__
    ):
        # Random.randint uses this same rejection sampler. Hoist validation and
        # width arithmetic; subclasses retain their virtual randint method.
        width = hi - low + 1
        below = py._randbelow  # type: ignore[attr-defined]  # stdlib's shared rejection sampler
        return [low + below(width) for _ in range(n)]
    return [py.randint(low, hi) for _ in range(n)]


def ints(
    n: int,
    low: int,
    high: int,
    *,
    endpoint: bool = False,
    seeds: Sequence[int] | None = None,
) -> IntArray:
    """*n* ints in ``[low, high)`` (or ``[low, high]`` when *endpoint*).

    An ndarray on the NumPy backend, a ``list`` on the stdlib fallback. *n* must
    be a non-negative integer and the interval must contain at least one value,
    including when *n* is zero. NumPy bounds must fit its default int64 dtype.

    Explicit *seeds* selects stateless SplitMix64-v1 draws, one unsigned 64-bit
    seed per result, within an int64 interval. These values differ from stdlib
    and NumPy streams and never advance either engine. Repeated keys repeat
    values; ordering and chunking do not change each key's result. Simulation
    use only; cryptographic draws belong to :mod:`unstd.rand.crypto`.
    """
    return _ints(_py, _gen, n, low, high, endpoint, seeds)


def _choices[T](
    py: _random.Random,
    gen: "_np.random.Generator | None",
    seq: Sequence[T],
    n: int,
    weights: Sequence[float] | None,
) -> list[T]:
    n = _size(n)
    items = list(seq)
    if gen is None:
        return py.choices(items, weights=weights, k=n)
    p = None
    if weights is not None:
        w = _np.asarray(weights, dtype=float)
        # Dividing by a negative total would flip signs past NumPy's own check.
        if (total := w.sum()) <= 0.0:
            msg = "Total of weights must be greater than zero"
            raise ValueError(msg)
        p = w / total
    # One C-level unbox of the whole index array, not an `int()` per element — ~1.8x.
    return list(map(items.__getitem__, gen.choice(len(items), size=n, p=p).tolist()))


def choices[T](
    seq: Sequence[T], n: int, *, weights: Sequence[float] | None = None
) -> list[T]:
    """*n* elements sampled **with** replacement (optionally *weights*-biased).

    *n* must be a non-negative integer. Weight validation and numeric limits are
    supplied by the selected engine; use :class:`Generator` to pin that engine.
    """
    return _choices(_py, _gen, seq, n, weights)


class Generator:
    """An independent stream with an explicitly selected bulk backend.

    ``Generator(seed)`` always uses stdlib, regardless of installed extras.
    ``backend="numpy"`` selects NumPy ``Generator(PCG64)`` for bulk draws and
    raises :class:`ImportError` if NumPy is absent. Its ``bit_generator`` option
    explicitly selects PCG64, PCG64DXSM, SFC64, or Philox; changing it changes the
    bulk stream. Scalar draws always use the instance's stdlib engine.
    On stdlib, scalar and bulk draws share state; on
    NumPy, they use separate engines seeded with the same value.

    Reproducibility requires the same backend, bit generator, calls, and
    compatible engine versions. Instances never reseed module-level draws or
    the crypto path. These generators are for simulations, never secrets.
    """

    __slots__ = ("_backend", "_gen", "_py")

    def __init__(
        self,
        seed: int | None = None,
        *,
        backend: Literal["stdlib", "numpy"] = "stdlib",
        bit_generator: Literal["PCG64", "PCG64DXSM", "SFC64", "Philox"] | None = None,
    ) -> None:
        """Create a stream using *seed* and an explicit bulk *backend*."""
        if backend not in ("stdlib", "numpy"):
            msg = "backend must be 'stdlib' or 'numpy'"
            raise ValueError(msg)
        if backend == "numpy" and not HAVE_NUMPY:
            msg = "NumPy backend requires the rand extra: pip install 'unstd[rand]'"
            raise ImportError(msg)
        if bit_generator is not None:
            if backend != "numpy":
                msg = "bit_generator requires backend='numpy'"
                raise ValueError(msg)
            if bit_generator not in ("PCG64", "PCG64DXSM", "SFC64", "Philox"):
                msg = "bit_generator must be 'PCG64', 'PCG64DXSM', 'SFC64', or 'Philox'"
                raise ValueError(msg)
        self._backend = backend
        if type(self) is Generator:
            # Validate NumPy first, as seed() does, then initialize MT only once.
            # No throwaway OS seed or additional per-instance slot is needed.
            self._gen = (
                _np.random.Generator(
                    getattr(_np.random, bit_generator or "PCG64")(seed)
                )
                if backend == "numpy"
                else None
            )
            self._py = _random.Random(seed)  # noqa: S311  # nosec B311 # simulation only
            return
        # Keep the initialized stdlib engine and virtual seed() hook available
        # to subclasses, including hooks that draw before calling super().seed().
        self._py = _random.Random()  # noqa: S311  # nosec B311 # non-crypto PRNG; security draws use `crypto`
        self._gen = (
            _np.random.Generator(getattr(_np.random, bit_generator)(0))
            if bit_generator is not None
            else None
        )
        self.seed(seed)

    @property
    def backend(self) -> Literal["stdlib", "numpy"]:
        """The explicitly selected bulk backend."""
        return self._backend

    def seed(self, n: int | None = None) -> None:
        """Reseed this instance, preserving both engines if validation fails."""
        replacement = (
            _np.random.Generator(
                type(self._gen.bit_generator)(n)
                if self._gen is not None
                else _np.random.PCG64(n)
            )
            if self._backend == "numpy"
            else None
        )
        self._py.seed(n)
        self._gen = replacement

    def random(self) -> float:
        """A float in ``[0.0, 1.0)`` from this instance's stdlib engine."""
        return self._py.random()

    def randint(self, a: int, b: int) -> int:
        """A random integer in ``[a, b]``, inclusive at both ends."""
        return self._py.randint(a, b)

    def uniform(self, a: float, b: float) -> float:
        """A stdlib ``uniform`` float between *a* and *b*."""
        return self._py.uniform(a, b)

    def choice[T](self, seq: Sequence[T]) -> T:
        """A single uniformly random element of a non-empty sequence."""
        return self._py.choice(seq)

    def shuffle[T](self, x: MutableSequence[T]) -> None:
        """Shuffle a mutable sequence in place."""
        py = self._py
        if (
            _native is not None
            and type(x) is list
            and type(py) is _random.Random
            and _random.Random.shuffle is _stdlib_shuffle
            and _random.Random._randbelow is _stdlib_randbelow  # type: ignore[attr-defined]
            and _random.Random.getrandbits is _stdlib_getrandbits
            and "shuffle" not in py.__dict__
            and "_randbelow" not in py.__dict__
            and "getrandbits" not in py.__dict__
        ):
            _native.shuffle(py.getrandbits, x)
            return
        self._py.shuffle(x)

    def sample[T](
        self, population: Sequence[T], k: int, *, counts: Sequence[int] | None = None
    ) -> list[T]:
        """*k* elements sampled without replacement, with stdlib *counts* support."""
        return self._py.sample(population, k, counts=counts)

    def floats(self, n: int, *, low: float = 0.0, high: float = 1.0) -> FloatArray:
        """*n* floats within finite bounds and span, using this instance's engine."""
        return _floats(self._py, self._gen, n, low, high)

    def ints(
        self,
        n: int,
        low: int,
        high: int,
        *,
        endpoint: bool = False,
        seeds: Sequence[int] | None = None,
    ) -> IntArray:
        """*n* integers, optionally stateless SplitMix64-v1 values from *seeds*.

        Without *seeds*, draw from this instance's selected engine. Explicit
        unsigned 64-bit *seeds* require an int64 interval and leave both engines
        untouched; these values use a different, chunk-independent stream.
        """
        return _ints(self._py, self._gen, n, low, high, endpoint, seeds)

    def choices[T](
        self, seq: Sequence[T], n: int, *, weights: Sequence[float] | None = None
    ) -> list[T]:
        """*n* elements sampled with replacement, using this instance's engine."""
        return _choices(self._py, self._gen, seq, n, weights)
