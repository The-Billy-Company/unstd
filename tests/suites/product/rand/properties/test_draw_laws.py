"""Generated bound, shape, membership, multiset, and reproducibility laws.

Each law holds within one selected backend. Same-seed twins must agree; NumPy
and stdlib streams are never compared with each other.
"""

from __future__ import annotations

from collections import Counter
from itertools import repeat
import random
import re
from typing import Literal

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend
from unstd import rand


Backend = Literal["stdlib", "numpy"]

seeds = st.integers(0, 2**64 - 1)
sizes = st.integers(0, 40)
int64 = st.one_of(st.integers(-3, 3), st.integers(-(2**62), 2**62))
finite = st.floats(-1e12, 1e12, allow_nan=False)


@pytest.fixture(scope="module", params=["stdlib", "numpy"])
def backend(request: pytest.FixtureRequest) -> Backend:
    if request.param == "numpy":
        require_backend("numpy")
    return request.param


def _pair(seed: int, backend: Backend) -> tuple[rand.Generator, rand.Generator]:
    return rand.Generator(seed, backend=backend), rand.Generator(seed, backend=backend)


@given(seed=seeds, n=sizes, a=finite, b=finite)
def test_floats_stay_in_closed_bounds_and_reproduce(
    backend: Backend, seed: int, n: int, a: float, b: float
) -> None:
    low, high = min(a, b), max(a, b)
    subject, twin = _pair(seed, backend)
    values = [float(v) for v in subject.floats(n, low=low, high=high)]
    assert len(values) == n
    assert all(low <= v <= high for v in values)
    assert values == [float(v) for v in twin.floats(n, low=low, high=high)]


@given(seed=seeds, n=sizes, a=int64, b=int64, endpoint=st.booleans())
def test_ints_respect_the_endpoint_rule_and_reproduce(
    backend: Backend, seed: int, n: int, a: int, b: int, endpoint: bool
) -> None:
    low, high = min(a, b), max(a, b) + (not endpoint)
    subject, twin = _pair(seed, backend)
    values = [int(v) for v in subject.ints(n, low, high, endpoint=endpoint)]
    assert len(values) == n
    top = high if endpoint else high - 1
    assert all(low <= v <= top for v in values)
    assert values == [int(v) for v in twin.ints(n, low, high, endpoint=endpoint)]


@given(seed=seeds, n=sizes, a=int64, b=int64, endpoint=st.booleans())
def test_empty_integer_intervals_fail_without_moving_the_stream(
    backend: Backend, seed: int, n: int, a: int, b: int, endpoint: bool
) -> None:
    low = max(a, b)
    high = min(a, b) - endpoint  # below low, or equal to it when exclusive
    subject, twin = _pair(seed, backend)
    with pytest.raises(ValueError, match="at least one value"):
        subject.ints(n, low, high, endpoint=endpoint)
    assert subject.random() == twin.random()
    assert [int(v) for v in subject.ints(5, 0, 100)] == [
        int(v) for v in twin.ints(5, 0, 100)
    ]


@given(
    seed=seeds,
    n=sizes,
    weights=st.lists(st.sampled_from([0.0, 0.5, 1.0, 7.0]), min_size=1, max_size=6),
)
def test_choices_draw_only_positively_weighted_members(
    backend: Backend, seed: int, n: int, weights: list[float]
) -> None:
    weights[-1] = weights[-1] or 1.0  # keep the total positive
    population = [f"item{i}" for i in range(len(weights))]
    allowed = {item for item, w in zip(population, weights, strict=True) if w > 0}
    subject, twin = _pair(seed, backend)
    drawn = subject.choices(population, n, weights=weights)
    assert len(drawn) == n
    assert set(drawn) <= allowed
    assert drawn == twin.choices(population, n, weights=weights)


@given(
    seed=seeds,
    counts=st.lists(st.integers(0, 4), min_size=1, max_size=6),
    data=st.data(),
)
def test_sample_is_a_sub_multiset_of_the_counted_population(
    backend: Backend, seed: int, counts: list[int], data: st.DataObject
) -> None:
    population = [f"p{i}" for i in range(len(counts))]
    pool = Counter(dict(zip(population, counts, strict=True)))
    k = data.draw(st.integers(0, pool.total()))
    subject, twin = _pair(seed, backend)
    reference = random.Random(seed)  # noqa: S311 — seeded stdlib contract oracle
    try:
        expected = reference.sample(population, k, counts=counts)
    except ValueError as error:
        # Early Python 3.12 rejects a zero total even when k=0; later versions
        # return []. Scalar sampling retains the running stdlib's contract.
        for stream in (subject, twin):
            with pytest.raises(type(error), match=f"^{re.escape(str(error))}$"):
                stream.sample(population, k, counts=counts)
        assert subject.random() == twin.random() == reference.random()
        return
    picked = subject.sample(population, k, counts=counts)
    assert len(picked) == k
    assert not Counter(picked) - pool
    assert picked == twin.sample(population, k, counts=counts) == expected


@given(seed=seeds, items=st.lists(st.integers(0, 5), max_size=30))
def test_shuffle_preserves_the_multiset_and_reproduces(
    backend: Backend, seed: int, items: list[int]
) -> None:
    subject, twin = _pair(seed, backend)
    left, right = items.copy(), items.copy()
    subject.shuffle(left)
    twin.shuffle(right)
    assert Counter(left) == Counter(items)
    assert left == right


@given(seed=seeds, other=seeds, n=st.integers(1, 20))
def test_reseeding_returns_to_the_seeded_stream(
    backend: Backend, seed: int, other: int, n: int
) -> None:
    subject, twin = _pair(seed, backend)
    first = (subject.random(), [float(v) for v in subject.floats(n)])
    subject.seed(other)
    subject.choices("abc", n)
    subject.seed(seed)
    assert (subject.random(), [float(v) for v in subject.floats(n)]) == first
    assert first == (twin.random(), [float(v) for v in twin.floats(n)])


@given(seed=seeds, n=sizes)
def test_module_draws_reproduce_after_reseeding(seed: int, n: int) -> None:
    rand.seed(seed)
    first = (
        rand.random(),
        [float(v) for v in rand.floats(n)],
        list(map(int, rand.ints(n, 0, 7))),
        rand.choices("xyz", n),
    )
    rand.seed(seed)
    assert first == (
        rand.random(),
        [float(v) for v in rand.floats(n)],
        list(map(int, rand.ints(n, 0, 7))),
        rand.choices("xyz", n),
    )
    assert set(first[3]) <= set("xyz")
    assert all(0 <= v < 7 for v in first[2])


def test_zero_count_draws_are_empty_on_every_shape(backend: Backend) -> None:
    subject = rand.Generator(1, backend=backend)
    assert list(subject.floats(0, low=-5.0, high=5.0)) == []
    assert list(subject.ints(0, 3, 3, endpoint=True)) == []
    assert subject.choices(list(repeat("x", 3)), 0, weights=[0.0, 1.0, 0.0]) == []
