"""Numerical contracts, stable ordering, layouts, and optional dependency failure.

References use Python scalar arithmetic or independent NumPy expressions. Tests
exercise public helpers through the same package boundary consumers import.
"""

from __future__ import annotations

from fractions import Fraction
import math as scalar
import subprocess
import sys

import pytest

from tests.support.environment.backends import require_backend
from unstd import math


@pytest.fixture
def np():
    return require_backend("numpy")


def test_module_imports_without_numpy_and_calls_explain_the_extra():
    script = """
import importlib.abc
import sys

class BlockNumpy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'numpy' or fullname.startswith('numpy.'):
            raise ImportError('NumPy intentionally unavailable')

sys.meta_path.insert(0, BlockNumpy())
from unstd import math
calls = (
    lambda: math.topk([1], 1),
    lambda: math.normalize_rows([[1.0]]),
    lambda: math.cosine_rows([[1.0]], [1.0]),
    lambda: math.mean_square([[1.0]]),
    lambda: math.pearson([1.0, 2.0], [1.0, 2.0]),
    lambda: math.weighted_mean([[1.0]], [1.0]),
    lambda: math.grouped_mean([[1.0]], [0], 1),
    lambda: math.frame([1.0], 1, 1),
    lambda: math.within([1], [1], atol=0),
)
for call in calls:
    try:
        call()
    except ImportError as error:
        assert 'unstd[math]' in str(error), str(error)
    else:
        raise AssertionError('a numeric helper accepted absent NumPy')
assert not any(name == 'numpy' or name.startswith('numpy.') for name in sys.modules)
"""
    result = subprocess.run(  # noqa: S603 — owned interpreter and fixed source
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4, 5, 6, 7])
def test_topk_matches_stable_python_sort_with_cutoff_ties(np, k):
    values = np.array([2.0, 9.0, 9.0, -1.0, 9.0, 2.0, 9.0])
    expected = sorted(range(len(values)), key=lambda i: -float(values[i]))[:k]
    actual = math.topk(values, k)
    assert actual.dtype == np.dtype(np.intp)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        ("int64", [-(2**63), 2**63 - 1, 2**63 - 2, 0]),
        ("uint64", [2**64 - 1, 2**64 - 2, 2**63, 0]),
        ("float64", [-scalar.inf, -0.0, 0.0, scalar.inf]),
    ],
)
def test_topk_retains_integer_precision_and_handles_infinite_values(np, dtype, values):
    source = np.array(values, dtype=dtype)
    expected = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    np.testing.assert_array_equal(math.topk(source, len(source)), expected)


@pytest.mark.parametrize("k", [-1, 4, 0.5, True, False])
def test_topk_rejects_invalid_counts(np, k):
    with pytest.raises((TypeError, ValueError)):
        math.topk(np.array([1, 2, 3]), k)


def test_topk_rejects_nan_even_when_nothing_is_requested(np):
    with pytest.raises(ValueError, match="NaN"):
        math.topk(np.array([1.0, scalar.nan]), 0)


def test_numpy_boolean_counts_do_not_bypass_integer_validation(np):
    for call in (
        lambda: math.topk([1, 2], np.bool_(True)),
        lambda: math.grouped_mean([[1]], [0], np.bool_(True)),
        lambda: math.frame([1, 2], np.bool_(True), 1),
        lambda: math.frame([1, 2], 1, np.bool_(True)),
    ):
        with pytest.raises(TypeError):
            call()


@pytest.mark.parametrize("eps", [0.0, 0.25])
def test_normalize_rows_preserves_zero_rows_and_uses_additive_epsilon(np, eps):
    values = np.array([[3, 4], [0, 0], [-5, 12]], dtype=np.int16)
    expected = np.array(
        [[3 / (5 + eps), 4 / (5 + eps)], [0, 0], [-5 / (13 + eps), 12 / (13 + eps)]]
    )
    actual = math.normalize_rows(values, eps=eps)
    assert actual.dtype == np.dtype(np.float64)
    np.testing.assert_allclose(actual, expected, rtol=1e-14, atol=0)
    np.testing.assert_array_equal(values, [[3, 4], [0, 0], [-5, 12]])


@pytest.mark.parametrize("eps", [0.0, 0.25])
def test_cosine_rows_matches_independent_scalar_formula(np, eps):
    rows = [[3, 4], [0, 0], [-4, 3]]
    query = [1, 2]
    expected = []
    for row in rows:
        denominator = (scalar.hypot(*row) + eps) * (scalar.hypot(*query) + eps)
        expected.append(
            sum(x * y for x, y in zip(row, query, strict=True)) / denominator
            if denominator
            else 0.0
        )
    actual = math.cosine_rows(rows, query, eps=eps)
    assert actual.dtype == np.dtype(np.float64)
    np.testing.assert_allclose(actual, expected, rtol=1e-14)
    np.testing.assert_array_equal(math.cosine_rows(rows, [0, 0], eps=eps), [0, 0, 0])


@pytest.mark.parametrize("eps", [-1, scalar.nan, scalar.inf])
def test_epsilon_must_be_finite_and_nonnegative(np, eps):
    for call in (
        lambda: math.normalize_rows([[1.0]], eps=eps),
        lambda: math.cosine_rows([[1.0]], [1.0], eps=eps),
    ):
        with pytest.raises(ValueError, match="eps"):
            call()


def test_mean_square_converts_before_multiplying_and_handles_empty_width(np):
    values = np.array([[30000, -30000], [3, 4]], dtype=np.int16)
    actual = math.mean_square(values)
    assert actual.dtype == np.dtype(np.float64)
    np.testing.assert_array_equal(actual, [900_000_000.0, 12.5])
    assert math.mean_square(np.empty((0, 4))).shape == (0,)
    empty = math.mean_square(np.empty((3, 0)))
    assert empty.shape == (3,)
    assert np.isnan(empty).all()


def test_pearson_matches_centered_python_reference_with_large_offset(np):
    x = [1e12 + v for v in [1, 4, 2, 8, 5]]
    y = [1e12 + v for v in [6, 2, 5, 1, 4]]
    exact_x, exact_y = [Fraction(v) for v in x], [Fraction(v) for v in y]
    dx = [v - sum(exact_x) / len(x) for v in exact_x]
    dy = [v - sum(exact_y) / len(y) for v in exact_y]
    expected = float(sum(a * b for a, b in zip(dx, dy, strict=True))) / scalar.sqrt(
        float(sum(a * a for a in dx) * sum(b * b for b in dy))
    )
    assert math.pearson(x, y) == pytest.approx(expected, rel=1e-13)
    assert math.pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


@pytest.mark.parametrize(
    ("x", "y"), [([], []), ([1], [2]), ([1, 1], [2, 3]), ([1, 2], [3, 3])]
)
def test_pearson_undefined_inputs_return_nan(np, x, y):
    assert scalar.isnan(math.pearson(x, y))


def test_pearson_detects_large_constant_vectors_despite_mean_rounding(np):
    constant = [1e100] * 10
    assert scalar.isnan(math.pearson(constant, constant))
    assert scalar.isnan(math.pearson(constant, list(range(10))))
    assert scalar.isnan(math.pearson(list(range(10)), constant))


def test_weighted_mean_matches_per_column_python_sums(np):
    values = [[1, 4, -2], [8, 3, 9], [2, 7, 5]]
    weights = [1, 0, 3]
    expected = [
        sum(row[j] * w for row, w in zip(values, weights, strict=True)) / sum(weights)
        for j in range(3)
    ]
    actual = math.weighted_mean(values, weights)
    assert actual.dtype == np.dtype(np.float64)
    np.testing.assert_array_equal(actual, expected)
    assert math.weighted_mean(np.empty((3, 0)), weights).shape == (0,)


@pytest.mark.parametrize(
    "weights", [[0, 0], [-1, 2], [1, scalar.nan], [1, scalar.inf], []]
)
def test_weighted_mean_rejects_undefined_weights(np, weights):
    with pytest.raises(ValueError, match="weight"):
        math.weighted_mean([[1.0], [3.0]], weights)


def test_grouped_mean_uses_dense_labels_and_zero_fills_missing_groups(np):
    actual = math.grouped_mean([[1, 3], [7, 9], [5, 1]], [2, 0, 2], 4)
    assert actual.dtype == np.dtype(np.float64)
    np.testing.assert_array_equal(actual, [[7, 9], [0, 0], [3, 2], [0, 0]])
    np.testing.assert_array_equal(
        math.grouped_mean(np.empty((0, 2)), [], 3), np.zeros((3, 2))
    )
    assert math.grouped_mean(np.empty((0, 2)), [], 0).shape == (0, 2)
    assert math.grouped_mean(np.empty((3, 0)), [0, 1, 0], 2).shape == (2, 0)


@pytest.mark.parametrize(
    ("labels", "groups"),
    [
        ([-1, 0], 2),
        ([0, 2], 2),
        ([0.0, 1.0], 2),
        ([True, False], 2),
        ([0], 2),
        ([0, 1], -1),
        ([0, 1], True),
        ([0, 1], 2.0),
    ],
)
def test_grouped_mean_rejects_invalid_labels_or_group_counts(np, labels, groups):
    with pytest.raises((TypeError, ValueError)):
        math.grouped_mean([[1.0], [3.0]], labels, groups)


@pytest.mark.parametrize("dtype", ["int16", "float32", "float64"])
def test_frame_has_exact_windows_readonly_aliasing_and_preserved_dtype(np, dtype):
    source = np.arange(15, dtype=dtype)[::-2]
    expected = np.array(
        [source[i : i + 3].tolist() for i in range(0, len(source) - 2, 2)]
    )
    actual = math.frame(source, 3, 2)
    assert actual.dtype == source.dtype
    assert not actual.flags.writeable
    assert np.shares_memory(actual, source)
    np.testing.assert_array_equal(actual, expected)
    source[0] = 99
    assert actual[0, 0] == 99
    with pytest.raises(ValueError, match="read-only"):
        actual[0, 0] = 0


def test_frame_short_and_readonly_inputs(np):
    short = math.frame([1, 2], 3, 1)
    assert short.shape == (0, 3)
    assert not short.flags.writeable
    source = np.arange(9, dtype=np.float32)
    source.flags.writeable = False
    result = math.frame(source, 2, 3)
    np.testing.assert_array_equal(result, [[0, 1], [3, 4], [6, 7]])
    assert np.shares_memory(source, result)
    assert not result.flags.writeable


@pytest.mark.parametrize(
    ("length", "hop"), [(0, 1), (2, 0), (-1, 2), (True, 1), (2, False), (1.5, 1)]
)
def test_frame_rejects_invalid_window_sizes(np, length, hop):
    with pytest.raises((TypeError, ValueError)):
        math.frame([1, 2, 3], length, hop)


@pytest.mark.parametrize("layout", ["strided", "fortran", "readonly"])
def test_numeric_operations_accept_array_layouts_without_mutation(np, layout):
    base = np.arange(48, dtype=np.float64).reshape(8, 6) - 7
    values = base[::-2, ::2] if layout == "strided" else np.array(base, order="F")
    if layout == "readonly":
        values.flags.writeable = False
    before = values.copy()
    query = np.arange(values.shape[1], dtype=np.float64) + 1
    weights = np.arange(values.shape[0], dtype=np.float64) + 1
    labels = np.arange(values.shape[0]) % 2
    np.testing.assert_allclose(
        math.normalize_rows(values),
        values / np.linalg.norm(values, axis=1, keepdims=True),
    )
    np.testing.assert_allclose(
        math.cosine_rows(values, query),
        (values @ query) / (np.linalg.norm(values, axis=1) * np.linalg.norm(query)),
    )
    np.testing.assert_allclose(math.mean_square(values), (values**2).mean(axis=1))
    np.testing.assert_allclose(
        math.weighted_mean(values, weights), np.average(values, axis=0, weights=weights)
    )
    np.testing.assert_allclose(
        math.grouped_mean(values, labels, 2),
        np.stack([values[labels == i].mean(axis=0) for i in range(2)]),
    )
    column = values[:, 0]
    np.testing.assert_array_equal(
        math.topk(column, 2), np.argsort(-column, kind="stable")[:2]
    )
    assert math.pearson(column, weights) == pytest.approx(
        float(np.corrcoef(column, weights)[0, 1])
    )
    np.testing.assert_array_equal(values, before)


@pytest.mark.parametrize("bad", [[[1 + 2j]], [["1"]], [[object()]]])
def test_numeric_operations_reject_nonreal_arrays(np, bad):
    for call in (
        lambda: math.topk(bad[0], 1),
        lambda: math.normalize_rows(bad),
        lambda: math.cosine_rows(bad, [1]),
        lambda: math.mean_square(bad),
        lambda: math.pearson(bad[0], [1]),
        lambda: math.weighted_mean(bad, [1]),
        lambda: math.grouped_mean(bad, [0], 1),
        lambda: math.frame(bad[0], 1, 1),
    ):
        with pytest.raises((TypeError, ValueError)):
            call()


def test_shape_mismatches_are_rejected(np):
    calls = (
        lambda: math.topk([[1, 2]], 1),
        lambda: math.normalize_rows([1, 2]),
        lambda: math.cosine_rows([[1, 2]], [1]),
        lambda: math.cosine_rows([[1, 2]], [[1, 2]]),
        lambda: math.mean_square([1, 2]),
        lambda: math.pearson([1, 2], [1]),
        lambda: math.pearson([[1, 2]], [[1, 2]]),
        lambda: math.weighted_mean([1, 2], [1, 1]),
        lambda: math.weighted_mean([[1], [2]], [[1], [1]]),
        lambda: math.grouped_mean([[1], [2]], [[0], [1]], 2),
        lambda: math.frame([[1, 2]], 1, 1),
    )
    for call in calls:
        with pytest.raises((TypeError, ValueError)):
            call()


@pytest.mark.parametrize("dtype", ["float32", "float64"])
@pytest.mark.parametrize("layout", ["contiguous", "strided", "readonly"])
def test_selected_calculation_dtype_matches_production_numpy_expressions(
    np, dtype, layout
):
    base = np.arange(48, dtype=dtype).reshape(8, 6) - 7
    values = base[::-2, ::2] if layout == "strided" else base
    if layout == "readonly":
        values.flags.writeable = False
    before = values.copy()
    query = np.arange(values.shape[1], dtype=dtype) + 1
    weights = np.arange(values.shape[0], dtype=dtype) + 1
    eps = 1e-6
    expected_normalized = values / (np.linalg.norm(values, axis=1, keepdims=True) + eps)
    expected_cosines = (values @ query) / (
        (np.linalg.norm(values, axis=1) + eps) * (np.linalg.norm(query) + eps)
    )
    outputs = (
        (math.normalize_rows(values, eps=eps, dtype=dtype), expected_normalized),
        (math.cosine_rows(values, query, eps=eps, dtype=dtype), expected_cosines),
        (math.mean_square(values, dtype=dtype), (values**2).mean(axis=1)),
        (
            math.weighted_mean(values, weights, dtype=dtype),
            np.average(values, axis=0, weights=weights),
        ),
    )
    tolerance = 2e-6 if dtype == "float32" else 1e-13
    for actual, expected in outputs:
        assert actual.dtype == np.dtype(dtype)
        assert actual.shape == expected.shape
        np.testing.assert_allclose(actual, expected, rtol=tolerance, atol=tolerance)
    np.testing.assert_array_equal(values, before)


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_selected_dtype_zero_and_empty_matrix_contracts(np, dtype):
    zeros = np.zeros((3, 2), dtype=dtype)
    zeros.flags.writeable = False
    outputs = (
        math.normalize_rows(zeros, dtype=dtype),
        math.cosine_rows(zeros, [0, 0], dtype=dtype),
        math.mean_square(zeros, dtype=dtype),
        math.weighted_mean(zeros, [1, 2, 3], dtype=dtype),
    )
    for output in outputs:
        assert output.dtype == np.dtype(dtype)
        np.testing.assert_array_equal(output, np.zeros_like(output))
    narrow = np.empty((3, 0), dtype=dtype)
    assert math.normalize_rows(narrow, dtype=dtype).shape == (3, 0)
    np.testing.assert_array_equal(
        math.cosine_rows(narrow, [], dtype=dtype), np.zeros(3, dtype=dtype)
    )
    squares = math.mean_square(narrow, dtype=dtype)
    assert squares.dtype == np.dtype(dtype)
    assert np.isnan(squares).all()
    pooled = math.weighted_mean(narrow, [1, 2, 3], dtype=dtype)
    assert pooled.shape == (0,)
    assert pooled.dtype == np.dtype(dtype)
    tall = np.empty((0, 3), dtype=dtype)
    assert math.normalize_rows(tall, dtype=dtype).shape == (0, 3)
    assert math.cosine_rows(tall, [1, 2, 3], dtype=dtype).shape == (0,)
    assert math.mean_square(tall, dtype=dtype).shape == (0,)


def test_float32_calculation_promotes_small_integers_before_arithmetic(np):
    values = np.array([[30000, -30000], [30000, 30000]], dtype=np.int16)
    before = values.copy()
    expected_square = [900_000_000.0, 900_000_000.0]
    actual_square = math.mean_square(values, dtype="float32")
    assert actual_square.dtype == np.dtype(np.float32)
    np.testing.assert_allclose(actual_square, expected_square, rtol=2e-6)
    normalized = math.normalize_rows(values, dtype="float32")
    np.testing.assert_allclose(
        normalized,
        [[2**-0.5, -(2**-0.5)], [2**-0.5, 2**-0.5]],
        rtol=2e-6,
    )
    np.testing.assert_allclose(
        math.cosine_rows(values, [30000, -30000], dtype="float32"),
        [1.0, 0.0],
        rtol=2e-6,
        atol=2e-6,
    )
    np.testing.assert_allclose(
        math.weighted_mean(
            values, np.array([30000, 30000], dtype=np.int16), dtype="float32"
        ),
        [30000, 0],
        rtol=2e-6,
    )
    np.testing.assert_array_equal(values, before)


@pytest.mark.parametrize("dtype", ["float16", "int32", "complex64", "unknown", None])
def test_calculation_dtype_rejects_unsupported_types(np, dtype):
    for call in (
        lambda: math.normalize_rows([[1.0]], dtype=dtype),
        lambda: math.cosine_rows([[1.0]], [1.0], dtype=dtype),
        lambda: math.mean_square([[1.0]], dtype=dtype),
        lambda: math.weighted_mean([[1.0]], [1.0], dtype=dtype),
    ):
        with pytest.raises((TypeError, ValueError), match="dtype"):
            call()
