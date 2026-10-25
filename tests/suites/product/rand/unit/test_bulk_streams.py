"""Bulk optimizations retain native values, arithmetic hooks and engine states.

The references are the selected engine's public uniform/randint methods. Full
state comparisons detect changes that equal outputs or bounds checks can miss,
including rejected integer samples and zero/degenerate float intervals.
"""

from __future__ import annotations

import math
import random
from typing import Literal, Self, override

import pytest

from unstd import rand
from unstd.rand import draw


Engine = Literal["PCG64", "PCG64DXSM", "SFC64", "Philox"]
_ENGINES: tuple[Engine, ...] = ("PCG64", "PCG64DXSM", "SFC64", "Philox")
_numpy = pytest.mark.skipif(not draw.HAVE_NUMPY, reason="NumPy requires the rand extra")
_FLOAT_BOUNDS = (
    (0.0, 1.0),
    (0, 1),
    (-0.0, 1.0),
    (-3.25, 8.75),
    (2.5, 2.5),
    (-0.0, -0.0),
    (-(2**100), 2**100),
)


@pytest.mark.parametrize("n", [0, 1, 17, 1024])
@pytest.mark.parametrize(("low", "high"), _FLOAT_BOUNDS)
def test_stdlib_float_values_and_state_match_uniform(n, low, high) -> None:
    subject = rand.Generator(173)
    reference = random.Random(173)  # noqa: S311 — seeded simulation reference
    expected = [reference.uniform(low, high) for _ in range(n)]
    actual = subject.floats(n, low=low, high=high)
    assert actual == expected
    assert all(type(value) is float for value in actual)
    assert [math.copysign(1.0, value) for value in actual] == [
        math.copysign(1.0, value) for value in expected
    ]
    assert subject._py.getstate() == reference.getstate()


@_numpy
@pytest.mark.parametrize("engine", _ENGINES)
@pytest.mark.parametrize("n", [0, 1, 17, 1024])
@pytest.mark.parametrize(("low", "high"), _FLOAT_BOUNDS)
def test_numpy_float_values_and_state_match_uniform(
    engine: Engine, n, low, high
) -> None:
    import numpy as np

    subject = rand.Generator(173, backend="numpy", bit_generator=engine)
    reference = np.random.Generator(getattr(np.random, engine)(173))
    expected = reference.uniform(low, high, size=n)
    actual = subject.floats(n, low=low, high=high)
    np.testing.assert_array_equal(actual, expected, strict=True)
    np.testing.assert_array_equal(np.signbit(actual), np.signbit(expected))
    assert subject._gen is not None
    np.testing.assert_equal(
        subject._gen.bit_generator.state, reference.bit_generator.state
    )
    untouched_scalar = random.Random(173)  # noqa: S311 — independent scalar stream
    assert subject._py.getstate() == untouched_scalar.getstate()


@pytest.mark.parametrize("n", [0, 1, 17, 1024])
@pytest.mark.parametrize("endpoint", [False, True])
@pytest.mark.parametrize(
    ("low", "high"),
    [
        (0, 1),
        (-13, 19),
        (-(2**63), 2**63 - 1),
        (2**200, 2**200 + 3),
        (-(2**201), 2**201),
    ],
)
def test_stdlib_integer_values_and_state_match_randint(n, low, high, endpoint) -> None:
    subject = rand.Generator(179)
    reference = random.Random(179)  # noqa: S311 — seeded simulation reference
    top = high if endpoint else high - 1
    expected = [reference.randint(low, top) for _ in range(n)]
    actual = subject.ints(n, low, high, endpoint=endpoint)
    assert actual == expected
    assert all(type(value) is int for value in actual)
    assert subject._py.getstate() == reference.getstate()


@pytest.mark.parametrize("n", [0, 1, 17, 1024])
def test_equal_inclusive_integer_bounds_keep_state_consumption(n) -> None:
    subject = rand.Generator(181)
    reference = random.Random(181)  # noqa: S311 — seeded simulation reference
    assert subject.ints(n, 7, 7, endpoint=True) == [
        reference.randint(7, 7) for _ in range(n)
    ]
    assert subject._py.getstate() == reference.getstate()


class _ArithmeticFloat(float):
    def __new__(cls, value: float) -> Self:
        result = super().__new__(cls, value)
        result.subtractions = 0
        return result

    @override
    def __sub__(self, other: float) -> float:
        self.subtractions += 1
        return float(self) - float(other) + self.subtractions / 1000


def test_stdlib_float_subclass_arithmetic_remains_per_draw() -> None:
    subject = rand.Generator(191)
    reference = random.Random(191)  # noqa: S311 — seeded simulation reference
    high, reference_high = _ArithmeticFloat(1.0), _ArithmeticFloat(1.0)
    assert subject.floats(13, high=high) == [
        reference.uniform(0.0, reference_high) for _ in range(13)
    ]
    assert high.subtractions == reference_high.subtractions == 13
    assert subject._py.getstate() == reference.getstate()


class _HookedRandom(random.Random):
    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self.uniform_calls = 0
        self.randint_calls = 0

    @override
    def uniform(self, a: float, b: float) -> float:
        self.uniform_calls += 1
        return super().uniform(a, b)

    @override
    def randint(self, a: int, b: int) -> int:
        self.randint_calls += 1
        return super().randint(a, b)


def test_custom_stdlib_engine_keeps_uniform_and_randint_hooks() -> None:
    subject = rand.Generator(193)
    subject._py = _HookedRandom(193)
    reference = _HookedRandom(193)
    assert subject.floats(11) == [reference.uniform(0.0, 1.0) for _ in range(11)]
    assert subject.ints(13, -7, 19) == [reference.randint(-7, 18) for _ in range(13)]
    assert subject._py.uniform_calls == reference.uniform_calls == 11
    assert subject._py.randint_calls == reference.randint_calls == 13
    assert subject._py.getstate() == reference.getstate()


def test_module_fallback_retains_mixed_stream_and_public_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(draw, "HAVE_NUMPY", False)
    monkeypatch.setattr(draw, "_gen", None)
    reference = random.Random(197)  # noqa: S311 — seeded simulation reference
    rand.seed(197)
    for n in (0, 1, 257):
        assert rand.floats(n, low=-2.0, high=7.0) == [
            reference.uniform(-2.0, 7.0) for _ in range(n)
        ]
        assert rand.ints(n, -(2**100), 2**100, endpoint=True) == [
            reference.randint(-(2**100), 2**100) for _ in range(n)
        ]
        assert rand.random() == reference.random()
        assert draw._py.getstate() == reference.getstate()
