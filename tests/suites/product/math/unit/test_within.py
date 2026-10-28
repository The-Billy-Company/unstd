"""Exact integer comparisons against independent arbitrary-precision distances."""

from __future__ import annotations

import pytest

from tests.support.environment.backends import require_backend
from unstd import math


@pytest.fixture(scope="module")
def np():
    return require_backend("numpy")


_DTYPES = ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"]


@pytest.mark.parametrize("left_dtype", _DTYPES)
@pytest.mark.parametrize("right_dtype", _DTYPES)
def test_every_integer_width_and_signedness_retains_exact_boundary_distances(
    np, left_dtype, right_dtype
):
    left_info, right_info = np.iinfo(left_dtype), np.iinfo(right_dtype)
    left_values = [int(left_info.min), int(left_info.max), 0, 1]
    right_values = [int(right_info.min), int(right_info.max), 0, 1]
    for left in left_values:
        for right in right_values:
            actual = np.asarray([left], dtype=left_dtype)
            expected = np.asarray([right], dtype=right_dtype)
            distance = abs(left - right)
            for tolerance in {
                0,
                1,
                2**53 - 1,
                2**53,
                2**63 - 1,
                2**63,
                2**64 - 1,
                2**64,
                2**65,
                2**100,
                distance,
                max(0, distance - 1),
            }:
                assert math.within(actual, expected, atol=tolerance) is (
                    distance <= tolerance
                ), (left_dtype, right_dtype, left, right, tolerance)


@pytest.mark.parametrize(
    "layout", ["contiguous", "strided", "reversed", "fortran", "endian"]
)
@pytest.mark.parametrize("left_dtype", ["int64", "uint64"])
@pytest.mark.parametrize("right_dtype", ["int64", "uint64"])
def test_vector_tolerances_layouts_and_readonly_inputs_are_exact_and_unchanged(
    np, layout, left_dtype, right_dtype
):
    rng = np.random.default_rng(981)
    left = rng.bit_generator.random_raw(48).view(left_dtype).reshape(6, 8)
    right = rng.bit_generator.random_raw(48).view(right_dtype).reshape(6, 8)
    if layout == "strided":
        left, right = left[::2, ::2], right[::2, ::2]
    elif layout == "reversed":
        left, right = left[::-1, ::-1], right[::-1, ::-1]
    elif layout == "fortran":
        left, right = np.asfortranarray(left), np.asfortranarray(right)
    elif layout == "endian":
        left, right = (
            left.astype(">" + left.dtype.str[1:]),
            right.astype(">" + right.dtype.str[1:]),
        )
    left.flags.writeable = right.flags.writeable = False
    before = left.tobytes(), right.tobytes()
    distances = [
        abs(int(a) - int(b)) for a, b in zip(left.flat, right.flat, strict=True)
    ]
    tolerances = np.asarray(
        [min(distance, 2**64 - 1) for distance in distances], dtype=np.uint64
    )
    tolerances = tolerances.reshape(left.shape)
    assert math.within(left, right, atol=tolerances) is all(
        distance <= 2**64 - 1 for distance in distances
    )
    tolerances.flat[0] = 0
    assert math.within(left, right, atol=tolerances) is all(
        distance <= int(tolerance)
        for distance, tolerance in zip(distances, tolerances.flat, strict=True)
    )
    assert (left.tobytes(), right.tobytes()) == before


def test_signed_tolerance_does_not_round_a_uint64_distance_through_float(np):
    maximum = 2**63 - 1
    assert not math.within(
        np.asarray([maximum + 1], dtype=np.uint64), [0], atol=maximum
    )
    assert math.within(np.asarray([maximum], dtype=np.uint64), [0], atol=maximum)
    assert not math.within([2**53 + 1], [0], atol=np.asarray([2**53], dtype=np.int64))


def test_mixed_signed_unsigned_equality_does_not_use_numpy_float_promotion(np):
    signed = np.asarray([2**63 - 1], dtype=np.int64)
    unsigned = np.asarray([2**63], dtype=np.uint64)
    assert not math.within(signed, unsigned, atol=0)
    assert not math.within(unsigned, signed, atol=0)
    assert math.within(signed, unsigned, atol=1)


def test_scalar_empty_and_large_python_integer_tolerance_contract(np):
    assert math.within(1, 1, atol=0)
    assert math.within(-1, np.uint64(2**64 - 1), atol=2**64)
    assert not math.within(-1, np.uint64(2**64 - 1), atol=2**64 - 1)
    assert math.within(np.empty((0, 3), dtype=int), np.empty((0, 3), dtype=int), atol=0)
    assert math.within(
        np.empty((0, 3), dtype=int), np.empty((0, 3), dtype=int), atol=2**100
    )
    assert math.within([[1, 2], [3, 4]], [[2, 3], [4, 5]], atol=1)


def test_deliberate_unsigned_wrap_does_not_emit_floating_diagnostics(np):
    with np.errstate(all="raise"):
        assert math.within(-(2**63), np.uint64(2**64 - 1), atol=2**65)
        assert not math.within(-(2**63), np.uint64(2**64 - 1), atol=2**64 - 1)
        assert math.within(-(2**63), 2**63 - 1, atol=2**64 - 1)


@pytest.mark.parametrize(
    "value", [True, False, 0.0, [1.0], [True], [1 + 0j], ["1"], [None]]
)
def test_noninteger_operands_and_tolerances_are_refused(np, value):
    with pytest.raises(TypeError, match="integer dtype"):
        math.within(value, value, atol=0)
    with pytest.raises(TypeError, match="integer dtype"):
        math.within([1], [1], atol=value)


def test_object_storage_and_subclasses_cannot_bypass_integer_validation(np):
    for source in (np.asarray([1], dtype=object), np.asarray([2**100], dtype=object)):
        with pytest.raises(TypeError, match="integer dtype"):
            math.within(source, source, atol=0)
        with pytest.raises(TypeError, match="integer dtype"):
            math.within([1], [1], atol=source)
    with pytest.raises(TypeError, match="integer dtype"):
        math.within([1], [1], atol=np.bool_(True))


@pytest.mark.parametrize("tolerance", [-1, [-1], [0, -1]])
def test_negative_tolerance_is_refused_before_comparison(np, tolerance):
    with pytest.raises(ValueError, match=r"nonnegative|match the operands"):
        math.within([0], [0], atol=tolerance)


def test_broadcasting_is_refused_even_for_vacuous_or_wide_tolerances(np):
    for tolerance in (0, 2**100):
        with pytest.raises(ValueError, match="equal shapes"):
            math.within([[1]], [1], atol=tolerance)
    with pytest.raises(ValueError, match="match the operands"):
        math.within([[1, 2], [3, 4]], [[1, 2], [3, 4]], atol=[0, 0])
    with pytest.raises(ValueError, match="match the operands"):
        math.within(np.empty((0, 3), dtype=int), np.empty((0, 3), dtype=int), atol=[0])
