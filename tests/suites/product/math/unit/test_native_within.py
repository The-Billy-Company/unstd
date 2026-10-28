"""The fused integer comparison agrees with exact distances and releases leases."""

from __future__ import annotations

import gc
import signal
import subprocess
import sys
import weakref

import pytest

from tests.support.environment.backends import require_backend


@pytest.fixture(scope="module")
def np():
    return require_backend("numpy")


@pytest.fixture(scope="module")
def native():
    return pytest.importorskip("unstd.math._math_native", exc_type=ImportError)


@pytest.mark.parametrize("dtype", ["int64", "uint64"])
@pytest.mark.parametrize("shape", [(), (0, 3), (1,), (3, 4), (2, 3, 4)])
def test_native_integer_shapes_extremes_and_unaligned_storage(np, native, dtype, shape):
    rng = np.random.default_rng(304)
    count = int(np.prod(shape)) if shape else 1
    data = rng.bit_generator.random_raw(count).view(dtype).reshape(shape)
    left = np.ndarray(shape, dtype=dtype, buffer=bytearray(data.nbytes + 1), offset=1)
    left[...] = data
    right = left.copy()
    if right.size:
        right.flat[0] = np.iinfo(dtype).min
        left.flat[0] = np.iinfo(dtype).max
    left.flags.writeable = right.flags.writeable = False
    initial = left.tobytes(), right.tobytes()
    for tolerance in (0, 1, 2**63 - 1, 2**63, 2**64 - 1):
        expected = all(
            abs(int(a) - int(b)) <= tolerance
            for a, b in zip(left.flat, right.flat, strict=True)
        )
        assert native.within(left, right, tolerance) is expected
        assert (
            native.within(left, right, np.asarray(tolerance, dtype=np.uint64))
            is expected
        )
    assert initial == (left.tobytes(), right.tobytes())


@pytest.mark.parametrize(
    "dtype", ["int8", "uint8", "int16", "uint16", "int32", "uint32", "int64", "uint64"]
)
def test_native_scalar_array_tolerances_retain_their_integer_width(np, native, dtype):
    left = np.asarray([0, 1, 2], dtype=np.int64)
    right = np.asarray([1, 2, 3], dtype=np.int64)
    assert native.within(left, right, np.asarray(1, dtype=dtype)) is True
    assert native.within(left, right, np.asarray(0, dtype=dtype)) is False


def test_native_equality_and_full_signed_difference_do_not_wrap(np, native):
    left = np.asarray([-(2**63), 2**63 - 1, -1, 0], dtype=np.int64)
    right = np.asarray([2**63 - 1, -(2**63), 0, 0], dtype=np.int64)
    assert native.within(left, left.copy(), 0) is True
    assert native.within(left, right, 2**64 - 2) is False
    assert native.within(left, right, 2**64 - 1) is True


@pytest.mark.parametrize("tolerance", [0, 3])
def test_native_comparison_checks_the_tail_after_a_signal_chunk(np, native, tolerance):
    left = np.arange(70_000, dtype=np.int64)
    right = left.copy()
    right[-1] += tolerance + 1
    assert native.within(left, right, tolerance) is False


def test_unsupported_tolerances_decline_before_operands_are_exported(np, native):
    class Unsupported:
        def __array__(self):
            pytest.fail("Native tolerance decline touched an unsupported operand")

    for tolerance in (
        True,
        False,
        0.0,
        -1,
        2**64,
        np.asarray(-1),
        np.asarray([0]),
        np.asarray(0.0),
    ):
        assert native.within(Unsupported(), Unsupported(), tolerance) is NotImplemented


def test_native_declines_other_integer_layouts_and_releases_all_input_leases(
    np, native
):
    class Subclass(np.ndarray):
        pass

    original = np.arange(12, dtype=np.int64).reshape(3, 4)
    for left, right in (
        (original, original.view(Subclass)),
        (original, original.astype(np.uint64)),
        (original[:, ::2], original[:, ::2]),
        (original, original[0]),
        (original.astype(np.int32), original.astype(np.int32)),
        (original.astype(">i8" if np.little_endian else "<i8"), original),
    ):
        assert native.within(left, right, 1) is NotImplemented
    del left, right
    for tolerance in (0, 1, np.asarray(1, dtype=np.int64)):
        left, right = original.copy(), original.copy()
        references = weakref.ref(left), weakref.ref(right)
        assert native.within(left, right, tolerance) is True
        del left, right
        gc.collect()
        assert all(reference() is None for reference in references)


@pytest.mark.parametrize("outcome", [True, False, NotImplemented])
def test_every_native_outcome_releases_operands_and_scalar_tolerance(
    np, native, outcome
):
    left = np.arange(8, dtype=np.int64)
    right = left.copy()
    if outcome is False:
        right[-1] += 2
    elif outcome is NotImplemented:
        right = right.astype(np.uint64)
    tolerance = np.asarray(1, dtype=np.int64)
    references = weakref.ref(left), weakref.ref(right), weakref.ref(tolerance)
    assert native.within(left, right, tolerance) is outcome
    del left, right, tolerance
    gc.collect()
    assert all(reference() is None for reference in references)


@pytest.mark.skipif(
    not hasattr(signal, "setitimer"), reason="Interval signals require POSIX"
)
@pytest.mark.usefixtures("np", "native")
def test_native_interruption_releases_both_array_leases():
    script = """
import gc
import signal
import weakref
import numpy as np
from unstd.math import _math_native as native

class Interrupted(Exception):
    pass

def interrupt(signum, frame):
    raise Interrupted()

assert native.within(np.asarray([1], dtype=np.int64), np.asarray([2], dtype=np.int64), 1)
left = np.arange(8_000_000, dtype=np.int64)
right = left + 1
references = weakref.ref(left), weakref.ref(right)
signal.signal(signal.SIGALRM, interrupt)
signal.setitimer(signal.ITIMER_REAL, 0.0005)
try:
    native.within(left, right, 3)
except Interrupted:
    pass
else:
    raise AssertionError('native comparison ignored interruption')
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
del left, right
gc.collect()
assert all(reference() is None for reference in references)
"""
    result = subprocess.run(  # noqa: S603 — fixed owned worker, bounded time and arrays.
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
