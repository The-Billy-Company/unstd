r"""Adversarial + parity tests for ``unstd.rand``.

These pin the three intents the module makes explicit:

- the **crypto** path keeps its token shapes and integer/sequence bounds;
- :func:`rand.seed` makes bulk + scalar draws reproducible on the **active**
  backend (numpy ``Generator(PCG64)`` or the stdlib ``random`` fallback);
- the stdlib fallback is what runs in this base install (no ``rand`` extra) — the
  bulk helpers return Python ``list``\\ s, and every draw stays in bounds.

Assertions are deliberately strict — never weakened to go green.
"""

from __future__ import annotations

import builtins
import importlib.util
import random
import secrets
from typing import TYPE_CHECKING, Literal, cast, get_type_hints

import pytest

from unstd import rand
from unstd.rand import crypto, draw


if TYPE_CHECKING:
    from collections.abc import Iterable


def _floats(x: Iterable[float]) -> list[float]:
    """Normalize a bulk result (ndarray | list) to a Python ``list[float]``."""
    return [float(v) for v in x]


# ── crypto shapes; primitive selection lives in security/structure ───────────


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


# ── independent streams and explicit backend contracts ────────────────────────


@pytest.fixture(
    params=[
        "stdlib",
        pytest.param(
            "numpy",
            marks=pytest.mark.skipif(
                not draw.HAVE_NUMPY, reason="NumPy requires the rand extra"
            ),
        ),
    ]
)
def backend(request: pytest.FixtureRequest) -> Literal["stdlib", "numpy"]:
    return cast("Literal['stdlib', 'numpy']", request.param)


@pytest.fixture
def module_backend(
    backend: Literal["stdlib", "numpy"], monkeypatch: pytest.MonkeyPatch
) -> Literal["stdlib", "numpy"]:
    available = backend == "numpy"
    monkeypatch.setattr(draw, "HAVE_NUMPY", available)
    monkeypatch.setattr(rand, "HAVE_NUMPY", available)
    monkeypatch.setattr(draw, "_gen", None)
    rand.seed(91)
    return backend


def _history(generator: rand.Generator) -> list[object]:
    shuffled = list(range(10))
    result: list[object] = [
        generator.random(),
        generator.randint(-3, 9),
        generator.uniform(-2.0, 3.0),
        _floats(generator.floats(5, low=-0.5, high=1.5)),
        list(generator.ints(5, -3, 7, endpoint=True)),
        generator.choice(["a", "b", "c"]),
        generator.choices(["a", "b", "c"], 9, weights=[1.0, 3.0, 2.0]),
        generator.sample(["a", "b", "c"], 4, counts=[3, 2, 1]),
    ]
    generator.shuffle(shuffled)
    result.append(shuffled)
    return result


def test_default_generator_is_stdlib_with_or_without_numpy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = rand.Generator(83)
    assert installed.backend == "stdlib"
    assert isinstance(installed.floats(0), list)
    expected = _history(installed)
    monkeypatch.setattr(draw, "HAVE_NUMPY", False)
    monkeypatch.setattr(draw, "_np", None)
    portable = rand.Generator(83)
    assert _history(portable) == expected
    with pytest.raises(ImportError, match=r"pip install 'unstd\[rand\]'"):
        rand.Generator(83, backend="numpy")


def test_generator_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="backend must be"):
        rand.Generator(1, backend=cast("Literal['stdlib', 'numpy']", "auto"))


def test_generator_reseeding_and_instances_are_independent(
    backend: Literal["stdlib", "numpy"],
) -> None:
    reference = rand.Generator(19, backend=backend)
    first = _history(reference)
    second = _history(reference)
    subject = rand.Generator(19, backend=backend)
    assert _history(subject) == first
    other = rand.Generator(19, backend=backend)
    assert _history(other) == first
    _history(other)
    other.seed(91)
    assert _history(subject) == second
    subject.seed(19)
    assert _history(subject) == first


def test_generator_does_not_change_module_or_global_stdlib_streams(
    backend: Literal["stdlib", "numpy"],
) -> None:
    rand.seed(43)
    expected = (rand.random(), _floats(rand.floats(9)))
    rand.seed(43)
    global_state = random.getstate()
    subject = rand.Generator(92, backend=backend)
    _history(subject)
    subject.seed(83)
    _history(subject)
    assert (rand.random(), _floats(rand.floats(9))) == expected
    assert random.getstate() == global_state


def test_generator_never_changes_crypto_primitives_or_entropy(
    backend: Literal["stdlib", "numpy"], monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[int] = []

    def entropy(size: int) -> bytes:
        requests.append(size)
        return b"Z" * size

    subject = rand.Generator(91, backend=backend)
    monkeypatch.setattr(random, "_urandom", entropy)
    subject.seed(21)
    _history(subject)
    assert not requests
    assert crypto.token_bytes is secrets.token_bytes
    assert crypto.token_bytes(16) == b"Z" * 16
    assert requests == [16]


def test_instance_scalar_surface_keeps_stdlib_arguments_and_sequences(
    backend: Literal["stdlib", "numpy"],
) -> None:
    subject = rand.Generator(67, backend=backend)
    reference = random.Random(67)  # noqa: S311 — seedable reference PRNG
    assert subject.random() == reference.random()
    assert subject.randint(a=-5, b=9) == reference.randint(a=-5, b=9)
    assert subject.uniform(a=3.0, b=-2.0) == reference.uniform(a=3.0, b=-2.0)
    assert subject.choice(seq=[1, 2, 3]) == reference.choice(seq=[1, 2, 3])
    expected = list(range(9))
    actual = expected.copy()
    reference.shuffle(x=expected)
    subject.shuffle(x=actual)
    assert actual == expected
    assert subject.sample(
        population=["a", "b"], k=4, counts=[3, 2]
    ) == reference.sample(population=["a", "b"], k=4, counts=[3, 2])


def test_legacy_successful_draws_keep_their_engine_sequences(
    module_backend: Literal["stdlib", "numpy"],
) -> None:
    py = random.Random(91)  # noqa: S311 — seedable reference PRNG
    assert rand.random() == py.random()
    assert rand.randint(-4, 19) == py.randint(-4, 19)
    assert rand.uniform(-2.0, 3.0) == py.uniform(-2.0, 3.0)
    if module_backend == "stdlib":
        expected_floats = [py.uniform(-0.5, 2.0) for _ in range(7)]
        expected_ints = [py.randint(-9, 20) for _ in range(7)]
        expected_choices = py.choices(["a", "b"], weights=[1.0, 3.0], k=9)
    else:
        import numpy as np

        gen = np.random.Generator(np.random.PCG64(91))
        expected_floats = gen.uniform(-0.5, 2.0, size=7).tolist()
        expected_ints = gen.integers(-9, 21, size=7).tolist()
        expected_choices = list(
            map(["a", "b"].__getitem__, gen.choice(2, size=9, p=[0.25, 0.75]).tolist())
        )
    assert _floats(rand.floats(7, low=-0.5, high=2.0)) == expected_floats
    assert list(rand.ints(7, -9, 21)) == expected_ints
    assert rand.choices(["a", "b"], 9, weights=[1.0, 3.0]) == expected_choices
    assert rand.random() == py.random()


def test_explicit_engines_match_legacy_draws_for_the_selected_backend(
    module_backend: Literal["stdlib", "numpy"],
) -> None:
    subject = rand.Generator(91, backend=module_backend)
    assert subject.random() == rand.random()
    assert _floats(subject.floats(9)) == _floats(rand.floats(9))
    assert list(subject.ints(9, -9, 12)) == list(rand.ints(9, -9, 12))
    assert subject.choice(["a", "b"]) == rand.choice(["a", "b"])
    assert subject.choices(["a", "b"], 9) == rand.choices(["a", "b"], 9)
    assert subject.random() == rand.random()


@pytest.mark.skipif(not draw.HAVE_NUMPY, reason="NumPy requires the rand extra")
@pytest.mark.parametrize("bad_seed", [-1, "invalid", [9]])
def test_failed_numpy_seed_preserves_scalar_and_bulk_state(bad_seed: object) -> None:
    # Negative/string seeds fail in NumPy; a list succeeds there but fails in stdlib.
    rand.seed(65)
    expected = (rand.random(), _floats(rand.floats(7)), list(rand.ints(7, 0, 9)))
    rand.seed(65)
    with pytest.raises((TypeError, ValueError)):
        rand.seed(cast("int", bad_seed))
    assert (
        rand.random(),
        _floats(rand.floats(7)),
        list(rand.ints(7, 0, 9)),
    ) == expected

    subject = rand.Generator(65, backend="numpy")
    reference = rand.Generator(65, backend="numpy")
    with pytest.raises((TypeError, ValueError)):
        subject.seed(cast("int", bad_seed))
    assert _history(subject) == _history(reference)


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "error"),
    [
        ("floats", (-1,), {}, ValueError),
        ("ints", (-1, 0, 10), {}, ValueError),
        ("choices", (["a", "b"], -1), {}, ValueError),
        ("floats", (3,), {"low": 2.0, "high": 1.0}, ValueError),
        ("floats", (0,), {"low": 2.0, "high": 1.0}, ValueError),
        ("ints", (3, 2, 1), {}, ValueError),
        ("ints", (0, 2, 1), {}, ValueError),
        ("ints", (0, 2, 2), {}, ValueError),
        ("ints", (3, 2, 1), {"endpoint": True}, ValueError),
        ("floats", (1.5,), {}, TypeError),
        ("ints", (1, 0.5, 2), {}, TypeError),
        ("choices", (["a", "b"], 1.5), {}, TypeError),
    ],
)
def test_invalid_bulk_shapes_fail_before_consuming_state(
    module_backend: Literal["stdlib", "numpy"],
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    subject = rand.Generator(91, backend=module_backend)
    with pytest.raises(error):
        getattr(subject, method)(*args, **kwargs)
    assert _history(subject) == _history(rand.Generator(91, backend=module_backend))

    with pytest.raises(error):
        getattr(rand, method)(*args, **kwargs)
    assert rand.random() == rand.Generator(91).random()


def test_degenerate_and_empty_valid_bulk_draws(
    backend: Literal["stdlib", "numpy"],
) -> None:
    subject = rand.Generator(53, backend=backend)
    assert _floats(subject.floats(3, low=2.0, high=2.0)) == [2.0] * 3
    assert list(subject.ints(3, 2, 2, endpoint=True)) == [2] * 3
    assert _floats(subject.floats(0)) == []
    assert list(subject.ints(0, 0, 1)) == []
    assert subject.choices([], 0) == []


@pytest.mark.parametrize("n", [0, 7])
@pytest.mark.parametrize(
    ("low", "high"),
    [
        (float("nan"), 1.0),
        (0.0, float("nan")),
        (float("nan"), float("nan")),
        (float("-inf"), 1.0),
        (0.0, float("inf")),
        (float("inf"), float("inf")),
        (float("-inf"), float("inf")),
        (-1e308, 1e308),
        (0.0, 10**1000),
    ],
)
def test_nonfinite_float_intervals_fail_without_consuming_either_engine(
    module_backend: Literal["stdlib", "numpy"], n: int, low: float, high: float
) -> None:
    subject = rand.Generator(91, backend=module_backend)
    with pytest.raises(
        ValueError, match="low, high, and their difference must be finite"
    ):
        subject.floats(n, low=low, high=high)
    assert _history(subject) == _history(rand.Generator(91, backend=module_backend))

    py_state = draw._py.getstate()
    numpy_state = draw._gen.bit_generator.state if draw._gen is not None else None
    with pytest.raises(
        ValueError, match="low, high, and their difference must be finite"
    ):
        rand.floats(n, low=low, high=high)
    assert draw._py.getstate() == py_state
    assert (
        draw._gen.bit_generator.state if draw._gen is not None else None
    ) == numpy_state


@pytest.mark.parametrize(
    ("low", "high"),
    [
        (-0.0, 0.0),
        (1e308, 1e308),
        (-1e308, -1e307),
        (0.0, 1e308),
        (5e-324, 1e-323),
    ],
)
def test_extreme_finite_float_intervals_keep_their_reference_engine_sequences(
    module_backend: Literal["stdlib", "numpy"], low: float, high: float
) -> None:
    if module_backend == "stdlib":
        py = random.Random(91)  # noqa: S311 — seedable reference PRNG
        expected = [py.uniform(low, high) for _ in range(7)]
    else:
        import numpy as np

        gen = np.random.Generator(np.random.PCG64(91))
        expected = gen.uniform(low, high, size=7).tolist()
    assert _floats(rand.floats(7, low=low, high=high)) == expected
    assert (
        _floats(
            rand.Generator(91, backend=module_backend).floats(7, low=low, high=high)
        )
        == expected
    )


def test_rand_public_annotations_support_runtime_introspection() -> None:
    for name in ("floats", "ints", "choices", "seed"):
        assert get_type_hints(getattr(rand, name))
    for name in (
        "__init__",
        "seed",
        "random",
        "randint",
        "uniform",
        "choice",
        "shuffle",
        "sample",
        "floats",
        "ints",
        "choices",
    ):
        assert get_type_hints(getattr(rand.Generator, name))


def test_import_without_numpy_keeps_explicit_streams_and_annotations_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def without_numpy(name: str, *args: object, **kwargs: object) -> object:
        if name == "numpy" or name.startswith("numpy."):
            msg = "fixture NumPy is absent"
            raise ImportError(msg)
        return original_import(name, *args, **kwargs)

    spec = importlib.util.spec_from_file_location("_draw_without_numpy", draw.__file__)
    assert spec is not None
    assert spec.loader is not None
    portable = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(builtins, "__import__", without_numpy)
    spec.loader.exec_module(portable)
    assert portable.HAVE_NUMPY is False
    assert _history(portable.Generator(83)) == _history(rand.Generator(83))
    for name in ("floats", "ints", "choices", "seed"):
        assert get_type_hints(getattr(portable, name))
        assert get_type_hints(getattr(portable.Generator, name))
    with pytest.raises(ImportError, match="NumPy backend requires the rand extra"):
        portable.Generator(83, backend="numpy")


@pytest.mark.usefixtures("stdlib_backend")
def test_stdlib_negative_seeds_keep_legacy_successful_behavior() -> None:
    reference = random.Random(-9)  # noqa: S311 — seedable reference PRNG
    rand.seed(-9)
    subject = rand.Generator(-9)
    expected = reference.random()
    assert rand.random() == expected
    assert subject.random() == expected


def test_failed_stdlib_seed_preserves_instance_state() -> None:
    subject = rand.Generator(71)
    with pytest.raises(TypeError):
        subject.seed(cast("int", object()))
    assert _history(subject) == _history(rand.Generator(71))
