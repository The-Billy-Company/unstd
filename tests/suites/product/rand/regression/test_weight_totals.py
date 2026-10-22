"""Weights without a positive total are rejected on every bulk engine.

The NumPy path normalized weights by their sum before NumPy validated them, so
an all-negative vector became valid probabilities and drew by magnitude.
"""

from __future__ import annotations

from typing import Literal

import pytest

from tests.support.backends import require_backend
from unstd import rand


@pytest.fixture(params=["stdlib", "numpy"])
def backend(request: pytest.FixtureRequest) -> Literal["stdlib", "numpy"]:
    if request.param == "numpy":
        require_backend("numpy")
    return request.param


@pytest.mark.parametrize("weights", [[-1.0, -2.0], [-1.0, 0.0], [0.0, -0.5, -0.5]])
def test_nonpositive_totals_fail_without_consuming_draws(
    backend: Literal["stdlib", "numpy"], weights: list[float]
) -> None:
    subject = rand.Generator(29, backend=backend)
    twin = rand.Generator(29, backend=backend)
    population = ["a", "b", "c"][: len(weights)]
    with pytest.raises(ValueError, match="greater than zero"):
        subject.choices(population, 4, weights=weights)
    assert subject.random() == twin.random()
    assert list(subject.floats(3)) == list(twin.floats(3))
