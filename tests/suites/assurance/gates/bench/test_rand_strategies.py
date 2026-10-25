"""Prospective sampling checks preserve sampling laws without seed parity claims."""

from __future__ import annotations

import pytest

from bench.profiles import rand_strategies as profile


def test_prospective_checks_accept_different_valid_subsets_and_orders():
    population = [object() for _ in range(8)]
    profile._check(population[:3], population, 3)
    profile._check(population[7:4:-1], population, 3)


@pytest.mark.parametrize("violation", ["duplicate", "foreign", "count"])
def test_prospective_checks_reject_invalid_sampling_results(violation):
    population = [object() for _ in range(8)]
    result = {
        "duplicate": [population[0], population[0]],
        "foreign": [population[0], object()],
        "count": population[:3],
    }[violation]
    with pytest.raises(AssertionError, match="strategy"):
        profile._check(result, population, 2)
