"""Generated ordering, geometric, pooling, grouping, and framing laws."""

from __future__ import annotations

import math as scalar

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend
from unstd import math


@pytest.fixture(scope="module")
def np():
    return require_backend("numpy")


@given(values=st.lists(st.integers(-(2**63), 2**63 - 1), max_size=100), data=st.data())
def test_topk_equals_python_stable_order_for_every_generated_cutoff(np, values, data):
    k = data.draw(st.integers(0, len(values)))
    array = np.array(values, dtype=np.int64)
    expected = sorted(range(len(values)), key=lambda i: values[i], reverse=True)[:k]
    np.testing.assert_array_equal(math.topk(array, k), expected)


@given(
    rows=st.lists(
        st.tuples(
            st.integers(-1000, 1000), st.integers(-1000, 1000), st.integers(-1000, 1000)
        ),
        min_size=1,
        max_size=30,
    )
)
def test_normalized_rows_are_unit_length_or_zero_and_scale_invariant(np, rows):
    values = np.array(rows, dtype=np.float64)
    result = math.normalize_rows(values)
    lengths = np.linalg.norm(result, axis=1)
    nonzero = np.any(values != 0, axis=1)
    np.testing.assert_allclose(lengths[nonzero], 1.0, rtol=1e-14)
    np.testing.assert_array_equal(lengths[~nonzero], 0.0)
    np.testing.assert_allclose(
        math.normalize_rows(values * 7), result, rtol=1e-14, atol=1e-14
    )
    np.testing.assert_allclose(
        math.mean_square(values),
        [sum(float(v) ** 2 for v in row) / 3 for row in rows],
        rtol=1e-14,
    )


@given(
    rows=st.lists(
        st.tuples(st.integers(-100, 100), st.integers(-100, 100)),
        min_size=1,
        max_size=30,
    ),
    query=st.tuples(st.integers(-100, 100), st.integers(-100, 100)),
)
def test_cosines_match_scalar_geometry_and_stay_bounded(np, rows, query):
    actual = math.cosine_rows(rows, query)
    expected = []
    for row in rows:
        denominator = scalar.hypot(*row) * scalar.hypot(*query)
        expected.append(
            sum(x * y for x, y in zip(row, query, strict=True)) / denominator
            if denominator
            else 0.0
        )
    np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-14)
    assert np.all(np.abs(actual) <= 1.0 + 1e-14)


@given(
    values=st.lists(st.integers(-1000, 1000), min_size=2, max_size=50),
    scale=st.integers(1, 100),
    offset=st.integers(-1000, 1000),
)
def test_pearson_identifies_affine_dependence_without_offset_cancellation(
    np, values, scale, offset
):
    if min(values) == max(values):
        assert scalar.isnan(math.pearson(values, values))
    else:
        assert math.pearson(
            values, [scale * x + offset for x in values]
        ) == pytest.approx(1.0, abs=1e-13)
        assert math.pearson(
            values, [-scale * x + offset for x in values]
        ) == pytest.approx(-1.0, abs=1e-13)


@given(
    items=st.lists(
        st.tuples(
            st.integers(-100, 100),
            st.integers(-100, 100),
            st.integers(1, 9),
            st.integers(0, 4),
        ),
        min_size=1,
        max_size=40,
    )
)
def test_weighted_and_grouped_means_match_scalar_partition_sums(np, items):
    values = np.array([[x, y] for x, y, _, _ in items], dtype=np.float64)
    weights = [w for _, _, w, _ in items]
    labels = [group for _, _, _, group in items]
    weighted = math.weighted_mean(values, weights)
    expected = [
        sum(row[j] * w for row, w in zip(values.tolist(), weights, strict=True))
        / sum(weights)
        for j in range(2)
    ]
    np.testing.assert_allclose(weighted, expected, rtol=1e-13, atol=1e-14)
    np.testing.assert_allclose(
        math.weighted_mean(values, [w * 3 for w in weights]),
        weighted,
        rtol=1e-13,
        atol=1e-14,
    )
    grouped = math.grouped_mean(values, labels, 6)
    for group in range(6):
        members = [[x, y] for x, y, _, label in items if label == group]
        expected_row = (
            [sum(row[j] for row in members) / len(members) for j in range(2)]
            if members
            else [0.0, 0.0]
        )
        np.testing.assert_allclose(grouped[group], expected_row, rtol=1e-13, atol=1e-14)


@given(
    values=st.lists(st.integers(-1000, 1000), max_size=100),
    length=st.integers(1, 20),
    hop=st.integers(1, 20),
)
def test_frame_matches_python_slices_at_every_valid_start(np, values, length, hop):
    source = np.array(values, dtype=np.int64)
    expected = [
        values[i : i + length] for i in range(0, max(0, len(values) - length + 1), hop)
    ]
    actual = math.frame(source, length, hop)
    assert actual.shape == (len(expected), length)
    assert actual.tolist() == expected
    assert not actual.flags.writeable
    if expected:
        assert np.shares_memory(actual, source)
