"""Range endpoints, empty inputs, weights, engine ownership, and secure-token edges.

Expected values come from interval/membership laws or from the selected stdlib
or NumPy engine called directly with the same seed — never from a prior run of
the wrapper. NumPy and stdlib streams are never compared with each other.
"""

from __future__ import annotations

import math
import random
import re
import secrets
import string
from typing import Literal

import pytest

from tests.support.environment.backends import require_backend
from unstd import rand
from unstd.rand import crypto


Backend = Literal["stdlib", "numpy"]


@pytest.fixture(params=["stdlib", "numpy"])
def backend(request: pytest.FixtureRequest) -> Backend:
    if request.param == "numpy":
        require_backend("numpy")
    return request.param


def _untouched(subject: rand.Generator, seed: int, backend: Backend) -> None:
    """Assert *subject* draws exactly what a fresh same-seed twin draws next."""
    twin = rand.Generator(seed, backend=backend)
    assert subject.random() == twin.random()
    assert [float(v) for v in subject.floats(4)] == [float(v) for v in twin.floats(4)]
    assert [int(v) for v in subject.ints(4, 0, 10)] == [
        int(v) for v in twin.ints(4, 0, 10)
    ]


# ── scalar endpoints follow stdlib ─────────────────────────────────────────────


def test_scalar_equal_and_reversed_ranges() -> None:
    subject = rand.Generator(3)
    assert [subject.randint(5, 5) for _ in range(20)] == [5] * 20
    assert [subject.uniform(2.5, 2.5) for _ in range(20)] == [2.5] * 20
    assert all(-2.0 <= subject.uniform(5.0, -2.0) <= 5.0 for _ in range(500))
    seen = {subject.randint(-1, 1) for _ in range(500)}
    assert seen == {-1, 0, 1}  # both endpoints are reachable; nothing outside


def test_reversed_randint_fails_like_stdlib_without_consuming() -> None:
    subject = rand.Generator(4)
    reference = random.Random(4)  # noqa: S311 — seedable reference PRNG
    with pytest.raises(ValueError, match="empty range"):
        reference.randint(6, 5)
    with pytest.raises(ValueError, match="empty range"):
        subject.randint(6, 5)
    _untouched(subject, 4, "stdlib")


def test_arbitrary_precision_scalar_and_stdlib_bulk_ints() -> None:
    subject = rand.Generator(8)
    low, high = -(2**200), 2**200
    assert all(low <= subject.randint(low, high) <= high for _ in range(200))
    big = subject.ints(64, 2**100, 2**100 + 3)
    assert isinstance(big, list)
    assert set(big) <= {2**100, 2**100 + 1, 2**100 + 2}
    assert all(type(v) is int for v in big)


# ── empty inputs ───────────────────────────────────────────────────────────────


def test_scalar_empty_and_oversized_inputs() -> None:
    subject = rand.Generator(5)
    with pytest.raises(IndexError):
        subject.choice([])
    with pytest.raises(
        ValueError, match="Sample larger than population or is negative"
    ):
        subject.sample([1, 2], 3)
    with pytest.raises(
        ValueError, match="Sample larger than population or is negative"
    ):
        subject.sample([1, 2], -1)
    assert subject.sample([1, 2], 0) == []
    assert subject.sample([], 0) == []
    empty: list[int] = []
    subject.shuffle(empty)
    assert empty == []
    single = [7]
    subject.shuffle(single)
    assert single == [7]


def test_empty_population_with_draws_raises_the_engine_error(backend: Backend) -> None:
    if backend == "stdlib":
        engine_error: type[Exception] = IndexError
        with pytest.raises(engine_error):
            random.Random(1).choices([], k=1)  # noqa: S311 — reference engine
    else:
        import numpy as np

        engine_error = ValueError
        with pytest.raises(engine_error):
            np.random.Generator(np.random.PCG64(1)).choice(0, size=1)
    with pytest.raises(engine_error):
        rand.Generator(1, backend=backend).choices([], 1)


# ── weights ────────────────────────────────────────────────────────────────────


def test_zero_weight_items_are_never_drawn(backend: Backend) -> None:
    subject = rand.Generator(12, backend=backend)
    assert subject.choices(["a", "b", "c"], 300, weights=[0.0, 2.5, 0.0]) == ["b"] * 300
    mixed = subject.choices(["a", "b", "c", "d"], 600, weights=[0.0, 1.0, 0.0, 3.0])
    assert set(mixed) == {"b", "d"}
    assert len(mixed) == 600


@pytest.mark.parametrize(
    "weights",
    [
        [0.0, 0.0],
        [1.0, math.nan],
        [math.nan, math.nan],
        [1.0, math.inf],
        [math.inf, math.inf],
        [1.0, -math.inf],
        [1.0],
        [1.0, 1.0, 1.0],
    ],
)
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_rejected_weights_fail_without_consuming_draws(
    backend: Backend, weights: list[float]
) -> None:
    subject = rand.Generator(17, backend=backend)
    message = (
        "Total of weights must be (greater than zero|finite)|"
        "The number of weights does not match the population"
        if backend == "stdlib"
        else "Total of weights must be greater than zero|"
        "Probabilities contain NaN|a and p must have same size"
    )
    with pytest.raises(ValueError, match=message):
        subject.choices(["a", "b"], 5, weights=weights)
    _untouched(subject, 17, backend)


def test_mixed_sign_weights_follow_the_selected_engine(backend: Backend) -> None:
    # A positive total with a negative member is engine-defined: stdlib's
    # cumulative bisection accepts it, while NumPy rejects negative probabilities.
    subject = rand.Generator(23, backend=backend)
    weights = [-1.0, 3.0]
    if backend == "stdlib":
        reference = random.Random(23)  # noqa: S311 — seedable reference PRNG
        expected = reference.choices(["a", "b"], weights=weights, k=8)
        assert subject.choices(["a", "b"], 8, weights=weights) == expected
    else:
        with pytest.raises(ValueError, match="non-negative"):
            subject.choices(["a", "b"], 8, weights=weights)
        _untouched(subject, 23, backend)


# ── engine ownership ───────────────────────────────────────────────────────────


def test_stdlib_instance_shares_one_engine_between_scalar_and_bulk() -> None:
    subject = rand.Generator(31)
    reference = random.Random(31)  # noqa: S311 — seedable reference PRNG
    bulk = subject.floats(5, low=-1.0, high=1.0)
    assert bulk == [reference.uniform(-1.0, 1.0) for _ in range(5)]
    assert subject.random() == reference.random()
    assert subject.ints(3, 0, 9) == [reference.randint(0, 8) for _ in range(3)]


def test_numpy_instance_keeps_scalar_and_bulk_engines_separate() -> None:
    np = require_backend("numpy")
    subject = rand.Generator(37, backend="numpy")
    scalar = random.Random(37)  # noqa: S311 — seedable reference PRNG
    bulk = np.random.Generator(np.random.PCG64(37))
    assert subject.floats(5).tolist() == bulk.uniform(0.0, 1.0, size=5).tolist()
    assert subject.random() == scalar.random()  # bulk did not advance stdlib
    assert subject.randint(0, 99) == scalar.randint(0, 99)
    assert subject.ints(4, 0, 9).tolist() == bulk.integers(0, 9, size=4).tolist()
    assert subject.choice("xyz") == scalar.choice("xyz")  # scalar did not advance PCG64


def test_numpy_bulk_dtypes_shapes_and_int64_limits() -> None:
    np = require_backend("numpy")
    subject = rand.Generator(41, backend="numpy")
    floats, ints = (
        subject.floats(6),
        subject.ints(6, -(2**63), 2**63 - 1, endpoint=True),
    )
    assert (floats.dtype, floats.shape) == (np.dtype(np.float64), (6,))
    assert (ints.dtype, ints.shape) == (np.dtype(np.int64), (6,))
    assert subject.floats(0).shape == (0,)
    with pytest.raises(ValueError, match="out of bounds for int64"):
        subject.ints(3, -(2**63) - 1, 0)
    with pytest.raises(ValueError, match="out of bounds for int64"):
        subject.ints(3, 0, 2**63, endpoint=True)


def test_stdlib_bulk_element_types() -> None:
    subject = rand.Generator(43)
    assert all(type(v) is float for v in subject.floats(8))
    assert all(type(v) is int for v in subject.ints(8, 0, 3))


# ── secure tokens ──────────────────────────────────────────────────────────────

_URLSAFE = re.compile(r"[A-Za-z0-9_-]*")


@pytest.mark.parametrize("n", [0, 1, 2, 3, 16, 31])
def test_token_lengths_and_alphabets(n: int) -> None:
    raw, hexed, url = (
        crypto.token_bytes(n),
        crypto.token_hex(n),
        crypto.token_urlsafe(n),
    )
    assert type(raw) is bytes
    assert len(raw) == n
    assert len(hexed) == 2 * n
    assert set(hexed) <= set(string.hexdigits.lower())
    assert len(url) == math.ceil(4 * n / 3)  # unpadded base64
    assert _URLSAFE.fullmatch(url)


def test_token_defaults_carry_32_bytes() -> None:
    assert len(crypto.token_bytes()) == 32
    assert len(crypto.token_hex()) == 64
    assert len(crypto.token_urlsafe()) == math.ceil(4 * 32 / 3)


def test_secure_integer_and_choice_edges() -> None:
    assert {crypto.below(1) for _ in range(50)} == {0}
    assert {crypto.bits(1) for _ in range(200)} <= {0, 1}
    assert all(0 <= crypto.bits(130) < 2**130 for _ in range(50))
    assert crypto.choice("z") == "z"
    for call, arg, message in [
        (crypto.below, 0, "Upper bound must be positive"),
        (crypto.below, -1, "Upper bound must be positive"),
        (crypto.bits, -1, "number of bits must be non-negative"),
    ]:
        with pytest.raises(ValueError, match=message):
            call(arg)
    with pytest.raises(ValueError, match="negative") as oracle:
        secrets.token_bytes(-1)
    with pytest.raises(type(oracle.value)) as actual:
        crypto.token_bytes(-1)
    assert actual.value.args == oracle.value.args
    with pytest.raises(IndexError):
        crypto.choice([])
