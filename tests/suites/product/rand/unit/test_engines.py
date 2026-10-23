"""Explicit NumPy stream identities, initialization and virtual seed hooks."""

from __future__ import annotations

import random
from typing import Literal, cast, override

import pytest

from unstd import rand
from unstd.rand import draw


Engine = Literal["PCG64", "PCG64DXSM", "SFC64", "Philox"]
_ENGINES: tuple[Engine, ...] = ("PCG64", "PCG64DXSM", "SFC64", "Philox")
_numpy = pytest.mark.skipif(not draw.HAVE_NUMPY, reason="NumPy requires the rand extra")


def _history(subject: rand.Generator) -> tuple[object, ...]:
    return (
        subject.random(),
        list(subject.floats(9, low=-2.0, high=3.0)),
        list(subject.ints(11, -7, 19, endpoint=True)),
        subject.choices(["a", "b", "c"], 13, weights=[1.0, 3.0, 2.0]),
        subject.randint(-3, 17),
    )


@_numpy
@pytest.mark.parametrize("engine", _ENGINES)
def test_explicit_numpy_engine_matches_reference_and_preserves_original_items(
    engine: Engine,
) -> None:
    import numpy as np

    subject = rand.Generator(91, backend="numpy", bit_generator=engine)
    reference = np.random.Generator(getattr(np.random, engine)(91))
    scalar = random.Random(91)  # noqa: S311 -- independent simulation reference
    assert subject.backend == "numpy"
    assert subject.random() == scalar.random()
    assert list(subject.floats(17, low=-2.0, high=3.0)) == list(
        reference.uniform(-2.0, 3.0, size=17)
    )
    assert list(subject.ints(19, -7, 21, endpoint=True)) == list(
        reference.integers(-7, 21, size=19, endpoint=True)
    )
    items = [object(), object(), object()]
    indices = reference.choice(3, size=23, p=[0.25, 0.5, 0.25]).tolist()
    actual = subject.choices(items, 23, weights=[1.0, 2.0, 1.0])
    assert all(
        item is items[index] for item, index in zip(actual, indices, strict=True)
    )
    assert subject.randint(-3, 17) == scalar.randint(-3, 17)


@_numpy
@pytest.mark.parametrize("engine", _ENGINES)
def test_reseed_keeps_selected_engine_and_independent_stream(engine: Engine) -> None:
    subject = rand.Generator(17, backend="numpy", bit_generator=engine)
    reference = rand.Generator(17, backend="numpy", bit_generator=engine)
    expected = _history(reference)
    assert _history(subject) == expected
    next_expected = _history(reference)
    other = rand.Generator(17, backend="numpy", bit_generator=engine)
    _history(other)
    other.seed(43)
    assert _history(subject) == next_expected
    subject.seed(17)
    assert _history(subject) == expected


@_numpy
@pytest.mark.parametrize("engine", _ENGINES)
@pytest.mark.parametrize("bad_seed", [-1, "invalid", [9]])
def test_invalid_reseed_preserves_both_engines(
    engine: Engine, bad_seed: object
) -> None:
    subject = rand.Generator(71, backend="numpy", bit_generator=engine)
    reference = rand.Generator(71, backend="numpy", bit_generator=engine)
    with pytest.raises((TypeError, ValueError)):
        subject.seed(cast("int", bad_seed))
    assert _history(subject) == _history(reference)


@_numpy
def test_omitted_numpy_engine_keeps_pcg64_history() -> None:
    default = rand.Generator(91, backend="numpy")
    explicit = rand.Generator(91, backend="numpy", bit_generator="PCG64")
    assert _history(default) == _history(explicit)
    rand.seed(91)
    expected = (
        rand.random(),
        list(rand.floats(9, low=-2.0, high=3.0)),
        list(rand.ints(11, -7, 19, endpoint=True)),
        rand.choices(["a", "b", "c"], 13, weights=[1.0, 3.0, 2.0]),
        rand.randint(-3, 17),
    )
    assert _history(rand.Generator(91, backend="numpy")) == expected


@pytest.mark.parametrize("engine", _ENGINES)
def test_explicit_engine_requires_numpy_backend(engine: Engine) -> None:
    with pytest.raises(ValueError, match="bit_generator requires backend='numpy'"):
        rand.Generator(91, bit_generator=engine)


@_numpy
@pytest.mark.parametrize("bad_engine", ["auto", "pcg64", "MT19937", 1, []])
def test_unknown_bit_generator_fails_before_seed_validation(bad_engine: object) -> None:
    with pytest.raises(ValueError, match="bit_generator must be"):
        rand.Generator(-1, backend="numpy", bit_generator=cast("Engine", bad_engine))


@pytest.mark.parametrize("engine", _ENGINES)
def test_missing_numpy_keeps_installation_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(draw, "HAVE_NUMPY", False)
    with pytest.raises(ImportError, match="NumPy backend requires the rand extra"):
        rand.Generator(-1, backend="numpy", bit_generator=engine)


@pytest.mark.parametrize("backend", ["stdlib", pytest.param("numpy", marks=_numpy)])
def test_normal_construction_seeds_stdlib_only_once(
    backend: Literal["stdlib", "numpy"], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    original = random.Random.seed

    def seed(self: random.Random, a: object = None, version: int = 2) -> None:
        calls.append(a)
        original(self, cast("int", a), version)

    monkeypatch.setattr(random.Random, "seed", seed)
    subject = rand.Generator(173, backend=backend)
    assert calls == [173]
    assert subject.random() == 0.3241267240578728


@pytest.mark.parametrize("backend", ["stdlib", pytest.param("numpy", marks=_numpy)])
def test_subclass_seed_hook_keeps_initialized_legacy_state(
    backend: Literal["stdlib", "numpy"],
) -> None:
    calls: list[tuple[object, ...]] = []

    class Hooked(rand.Generator):
        @override
        def seed(self, n: int | None = None) -> None:
            calls.append((n, self.backend, self._gen, self.random()))
            super().seed(n)

    subject = Hooked(173, backend=backend)
    assert len(calls) == 1
    n, selected, before_numpy, before_scalar = calls[0]
    assert n == 173
    assert selected == backend
    assert before_numpy is None
    assert isinstance(before_scalar, float)
    assert 0 <= before_scalar < 1
    assert _history(subject) == _history(rand.Generator(173, backend=backend))


@_numpy
@pytest.mark.parametrize("engine", _ENGINES)
def test_subclass_seed_hook_keeps_explicit_engine_on_reseed(engine: Engine) -> None:
    calls: list[int | None] = []

    class Hooked(rand.Generator):
        @override
        def seed(self, n: int | None = None) -> None:
            calls.append(n)
            super().seed(n)

    subject = Hooked(173, backend="numpy", bit_generator=engine)
    expected = _history(rand.Generator(173, backend="numpy", bit_generator=engine))
    assert _history(subject) == expected
    subject.seed(173)
    assert calls == [173, 173]
    assert _history(subject) == expected


@_numpy
def test_numpy_constructor_validates_seed_before_stdlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    original = random.Random.seed

    def seed(self: random.Random, a: object = None, version: int = 2) -> None:
        calls.append(a)
        original(self, cast("int", a), version)

    monkeypatch.setattr(random.Random, "seed", seed)
    with pytest.raises(ValueError, match="non-negative"):
        rand.Generator(-1, backend="numpy")
    assert calls == []
    with pytest.raises(TypeError):
        rand.Generator(cast("int", [9]), backend="numpy")
    assert calls == [[9]]
