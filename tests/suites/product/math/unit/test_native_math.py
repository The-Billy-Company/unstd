"""Native math agrees with independent NumPy formulas on admitted layouts.

The extension is optional. These tests exercise it directly when built, so a
Python fallback cannot silently make a broken native path appear correct.
"""

from __future__ import annotations

from importlib import import_module

import pytest

from tests.support.environment.backends import require_backend
from unstd import math as numeric


@pytest.fixture(scope="module")
def np():
    return require_backend("numpy")


@pytest.fixture(scope="module")
def native():
    return pytest.importorskip("unstd.math._math_native", exc_type=ImportError)


@pytest.mark.parametrize("dtype", ["float32", "float64"])
@pytest.mark.parametrize(
    "layout", ["contiguous", "strided", "reversed", "fortran", "broadcast"]
)
def test_native_row_kernels_match_formulas_and_never_mutate(np, native, dtype, layout):
    source = np.arange(1, 145, dtype=dtype).reshape(12, 12)
    if layout == "strided":
        source = source[::2, ::2]
    elif layout == "reversed":
        source = source[::-1, ::-1]
    elif layout == "fortran":
        source = np.asfortranarray(source)
    elif layout == "broadcast":
        source = np.broadcast_to(source[0], (7, 12))
    source.flags.writeable = False
    initial = source.copy()
    query = np.arange(1, source.shape[1] + 1, dtype=dtype)[::-1]
    query.flags.writeable = False
    weights = np.arange(1, source.shape[0] + 1, dtype=dtype)[::-1]
    weights.flags.writeable = False
    eps = 0.125
    norm = np.linalg.norm(source, axis=1)
    expected = (
        source / (norm[:, None] + eps),
        source @ query / ((norm + eps) * (np.linalg.norm(query) + eps)),
        np.mean(source**2, axis=1),
        np.average(source, axis=0, weights=weights),
    )
    actual = (
        native.normalize_rows(source, eps, dtype),
        native.cosine_rows(source, query, eps, dtype),
        native.mean_square(source, dtype),
        native.weighted_mean(source, weights, dtype),
    )
    for result, reference in zip(actual, expected, strict=True):
        assert result is not NotImplemented
        assert result.dtype == np.dtype(dtype)
        np.testing.assert_allclose(result, reference, rtol=3e-6, atol=1e-6)
        assert not np.shares_memory(result, source)
    np.testing.assert_array_equal(source, initial)


@pytest.mark.parametrize("dtype", ["float32", "float64"])
@pytest.mark.parametrize("shape", [(0, 0), (0, 5), (3, 0), (1, 1)])
def test_native_empty_shapes_and_zero_rows_match_public_contract(
    np, native, dtype, shape
):
    source = np.zeros(shape, dtype=dtype)
    query = np.zeros(shape[1], dtype=dtype)
    unit = native.normalize_rows(source, 0.0, dtype)
    cosine = native.cosine_rows(source, query, 0.0, dtype)
    square = native.mean_square(source, dtype)
    assert unit.shape == shape
    assert cosine.shape == (shape[0],)
    assert square.shape == (shape[0],)
    np.testing.assert_array_equal(unit, source)
    np.testing.assert_array_equal(cosine, np.zeros(shape[0], dtype=dtype))
    if shape[1] == 0:
        assert np.isnan(square).all()
    else:
        np.testing.assert_array_equal(square, np.zeros(shape[0], dtype=dtype))


@pytest.mark.parametrize(
    "dtype",
    [
        "int8",
        "uint8",
        "int16",
        "uint16",
        "int32",
        "uint32",
        "int64",
        "uint64",
        "float32",
        "float64",
    ],
)
def test_native_topk_exact_integer_order_and_stable_ties(np, native, dtype):
    if np.dtype(dtype).kind in "iu":
        limits = np.iinfo(dtype)
        values = [limits.max, limits.max, 0, limits.min, limits.max - 1]
    else:
        values = [float("inf"), 2.0, -0.0, 0.0, -float("inf"), 2.0]
    source = np.array(values, dtype=dtype)[::-1]
    source.flags.writeable = False
    initial = source.copy()
    for k in range(source.size + 1):
        expected = sorted(
            range(len(source)), key=lambda index: source[index], reverse=True
        )[:k]
        result = native.topk(source, k)
        assert result is not NotImplemented
        assert result.dtype == np.dtype(np.intp)
        np.testing.assert_array_equal(result, expected)
    np.testing.assert_array_equal(source, initial)


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_native_groups_include_absent_labels_and_support_reversed_storage(
    np, native, dtype
):
    source = np.arange(96, dtype=dtype).reshape(12, 8)[::-1, ::-1]
    labels = np.array([0, 1, 0, 3, 3, 1, 1, 0, 0, 3, 1, 3], dtype=np.int64)[::-1]
    source.flags.writeable = False
    labels.flags.writeable = False
    expected = np.zeros((5, 8), dtype=np.float64)
    for label in range(5):
        members = source[labels == label]
        if len(members):
            expected[label] = members.mean(axis=0, dtype=np.float64)
    result = native.grouped_mean(source, labels, 5)
    assert result is not NotImplemented
    assert result.dtype == np.dtype(np.float64)
    np.testing.assert_allclose(result, expected, rtol=1e-14, atol=1e-14)


def test_native_declines_unsupported_objects_instead_of_reinterpreting_them(np, native):
    ordinary = np.ones((2, 3), dtype=np.float64)

    class Subclass(np.ndarray):
        pass

    for values in (
        [[1.0, 2.0, 3.0]],
        ordinary.view(Subclass),
        ordinary.astype(object),
        ordinary.astype(np.complex128),
        ordinary.astype(">f8" if np.little_endian else "<f8"),
    ):
        assert native.normalize_rows(values, 0.0, "float64") is NotImplemented
    assert (
        native.mean_square(np.ones((2, 3), dtype=np.int64), "float64") is NotImplemented
    )
    assert native.topk(np.ones(3, dtype=np.float64), np.int64(1)) is NotImplemented
    assert native.topk(np.ones(3, dtype=np.float64), True) is NotImplemented
    assert native.topk(np.ones(3, dtype=np.float64), 1 << 100) is NotImplemented
    assert native.topk(np.ones(3, dtype=np.float64), -(1 << 100)) is NotImplemented


def test_native_invalid_inputs_cannot_reach_unchecked_buffer_arithmetic(np, native):
    source = np.ones((2, 3), dtype=np.float64)
    invalid_calls = (
        lambda: native.topk(source, 1),
        lambda: native.topk(source[0], -1),
        lambda: native.topk(source[0], 4),
        lambda: native.topk(np.array([np.nan]), 0),
        lambda: native.normalize_rows(source, -1.0, "float64"),
        lambda: native.normalize_rows(source, np.inf, "float64"),
        lambda: native.normalize_rows(source, 0.0, "int32"),
        lambda: native.cosine_rows(source, np.ones(4), 0.0, "float64"),
        lambda: native.weighted_mean(source, np.ones(3), "float64"),
        lambda: native.weighted_mean(source, np.array([1.0, -1.0]), "float64"),
        lambda: native.weighted_mean(source, np.array([0.0, 0.0]), "float64"),
        lambda: native.weighted_mean(source, np.array([np.inf, 1.0]), "float64"),
        lambda: native.grouped_mean(source, np.array([0, -1]), 2),
        lambda: native.grouped_mean(source, np.array([0, 2]), 2),
        lambda: native.grouped_mean(source, np.array([0]), 2),
    )
    for call in invalid_calls:
        with pytest.raises((TypeError, ValueError)):
            call()


def test_native_module_can_be_imported_again_without_reusing_python_sentinels(native):
    assert import_module("unstd.math._math_native") is native


def test_native_float32_narrows_before_division_not_after_it(np, native):
    source = np.array([[1e50, 1.0], [-1e50, -1.0]], dtype=np.float64)
    with np.errstate(all="ignore"):
        narrowed = source.astype(np.float32)
        denominator = np.linalg.norm(narrowed, axis=1, keepdims=True)
        expected = np.divide(narrowed, denominator)
        assert native.normalize_rows(source, 0.0, "float32") is NotImplemented
        actual = numeric.normalize_rows(source, dtype="float32")
    np.testing.assert_array_equal(actual, expected)


def test_cosine_denominator_uses_selected_precision_before_widening(np, native):
    source = np.array([[1e19]], dtype=np.float32)
    query = source[0]
    assert native.cosine_rows(source, query, 1e19, "float32") is NotImplemented
    with np.errstate(over="raise"):
        with pytest.raises(FloatingPointError, match="overflow"):
            numeric.cosine_rows(source, query, eps=1e19, dtype="float32")
        wide = native.cosine_rows(source, query, 1e19, "float64")
    assert wide is not NotImplemented
    np.testing.assert_allclose(wide, [0.25], rtol=1e-6)


def test_native_empty_inferred_float_labels_are_vacuously_valid(np, native):
    source = np.empty((0, 3), dtype=np.float64)
    labels = np.asarray([])
    result = native.grouped_mean(source, labels, 4)
    assert result is not NotImplemented
    assert result.shape == (4, 3)
    np.testing.assert_array_equal(result, np.zeros((4, 3)))


@pytest.mark.parametrize("dtype", ["float32", "float64"])
@pytest.mark.parametrize("rows", [1, 8, 256])
@pytest.mark.parametrize("layout", ["contiguous", "strided", "fortran"])
def test_public_dispatch_and_disabled_native_have_the_same_numeric_contract(
    np, native, monkeypatch, dtype, rows, layout
):
    source = np.arange(rows * 128, dtype=dtype).reshape(rows, 128)[:, ::2]
    if layout == "contiguous":
        source = np.ascontiguousarray(source)
    elif layout == "fortran":
        source = np.asfortranarray(source)
    source.flags.writeable = False
    query = np.arange(64, dtype=dtype)[::-1]
    weights = np.arange(1, rows + 1, dtype=dtype)

    def compute():
        return (
            numeric.normalize_rows(source, eps=1e-9, dtype=dtype),
            numeric.cosine_rows(source, query, eps=1e-9, dtype=dtype),
            numeric.mean_square(source, dtype=dtype),
            numeric.weighted_mean(source, weights, dtype=dtype),
        )

    accelerated = compute()
    with monkeypatch.context() as context:
        context.setattr(numeric, "_native", None)
        fallback = compute()
    for actual, expected in zip(accelerated, fallback, strict=True):
        assert actual.shape == expected.shape
        assert actual.dtype == expected.dtype
        np.testing.assert_allclose(actual, expected, rtol=5e-6, atol=1e-6)


@pytest.mark.parametrize(
    "dtype", ["int8", "int16", "int64", "uint64", "float32", "float64"]
)
@pytest.mark.parametrize(
    ("size", "length", "hop"), [(0, 4, 2), (3, 4, 2), (8, 4, 2), (11, 3, 5)]
)
def test_native_frame_retains_dtype_readonly_aliasing_and_source_lifetime(
    np, native, dtype, size, length, hop
):
    source = np.arange(size, dtype=dtype)
    actual = native.frame(source, length, hop)
    assert actual is not NotImplemented
    expected = [
        source[index : index + length].tolist()
        for index in range(0, max(0, size - length + 1), hop)
    ]
    assert actual.shape == (len(expected), length)
    assert actual.dtype == np.dtype(dtype)
    assert actual.tolist() == expected
    assert not actual.flags.writeable
    if expected:
        assert np.shares_memory(actual, source)
        source[0] = 19
        assert actual[0, 0] == 19
        with pytest.raises(ValueError, match="WRITEABLE"):
            actual.flags.writeable = True
        del source
        assert actual[0, 0] == 19
        np.testing.assert_array_equal(actual[0, 1:], np.arange(1, length, dtype=dtype))


def test_native_frame_declines_noncontiguous_and_validates_window_sizes(np, native):
    source = np.arange(12, dtype=np.float32)
    assert native.frame(source[::2], 2, 1) is NotImplemented
    assert native.frame(source, np.int64(2), 1) is NotImplemented
    for length, hop in ((0, 1), (-1, 1), (1, 0), (1, -1)):
        with pytest.raises((TypeError, ValueError)):
            native.frame(source, length, hop)


@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_bulk_workflows_keep_vectorized_reductions_instead_of_scalar_native(
    np, native, dtype
):
    source = np.ones((4096, 128), dtype=dtype)
    query = np.ones(128, dtype=dtype)
    weights = np.ones(4096, dtype=dtype)
    assert native.normalize_rows(source, 0.0, dtype) is NotImplemented
    assert native.cosine_rows(source, query, 0.0, dtype) is NotImplemented
    assert native.mean_square(source, dtype) is NotImplemented
    assert native.weighted_mean(source, weights, dtype) is NotImplemented
    np.testing.assert_array_equal(
        numeric.mean_square(source, dtype=dtype), np.ones(4096)
    )
    np.testing.assert_array_equal(
        numeric.weighted_mean(source, weights, dtype=dtype), np.ones(128)
    )


@pytest.mark.parametrize(
    "case", ["narrow_overflow", "nonfinite_divide", "norm_overflow", "divide_underflow"]
)
def test_public_native_and_python_paths_preserve_numpy_raise_policy(
    np, native, monkeypatch, case
):
    def operation():
        if case == "narrow_overflow":
            return numeric.normalize_rows(np.array([[1e50]]), dtype="float32")
        if case == "nonfinite_divide":
            return numeric.normalize_rows(np.array([[np.inf]]))
        if case == "norm_overflow":
            return numeric.cosine_rows(np.array([[1e308]]), np.array([1e308]))
        return numeric.normalize_rows(np.array([[1.0, 1e-320]]), eps=0.5)

    with monkeypatch.context() as context:
        context.setattr(numeric, "_native", None)
        with np.errstate(all="raise"), pytest.raises(FloatingPointError):
            operation()
    with np.errstate(all="raise"), pytest.raises(FloatingPointError):
        operation()


def test_subnormal_division_obeys_the_independent_numpy_error_policy(np, native):
    values = np.array([[1.0, 1e-320]])
    with np.errstate(all="raise"), pytest.raises(FloatingPointError):
        np.divide(values, np.array([[1.5]]))
    with np.errstate(all="raise"), pytest.raises(FloatingPointError):
        numeric.normalize_rows(values, eps=0.5)


def test_public_native_path_preserves_numpy_invalid_warning(np, native):
    with np.errstate(invalid="warn"), pytest.warns(RuntimeWarning, match="invalid"):
        result = numeric.normalize_rows(np.array([[np.inf]]))
    assert np.isnan(result).all()


def test_numpy_error_callback_runs_once_for_either_dispatch_path(
    np, native, monkeypatch
):
    observed = []

    def callback(message, flags):
        observed.append((message, flags))

    previous = np.geterrcall()
    np.seterrcall(callback)
    try:
        with np.errstate(invalid="call"):
            with monkeypatch.context() as context:
                context.setattr(numeric, "_native", None)
                expected = numeric.normalize_rows(np.array([[np.inf]]))
            reference_events = observed.copy()
            observed.clear()
            actual = numeric.normalize_rows(np.array([[np.inf]]))
        assert len(reference_events) == 1
        assert observed == reference_events
        np.testing.assert_array_equal(actual, expected)
    finally:
        np.seterrcall(previous)
