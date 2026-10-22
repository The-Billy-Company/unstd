"""Explicit portable arrays retain struct validation and owned Python values across extras."""

from __future__ import annotations

import math
import struct
from typing import get_type_hints

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.pack import binpack


_HAVE_NUMPY = binpack._HAVE_NUMPY


@pytest.fixture(params=[False, True], ids=["base", "numpy-installed"])
def array_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Exercise portable helpers in base and NumPy-enabled configurations."""
    if request.param:
        if not _HAVE_NUMPY:
            pytest.skip("numpy (`pack` extra) not installed")
    else:
        monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
        monkeypatch.setattr(binpack, "np", None)


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize(
    ("dtype", "fmt", "values"),
    [
        ("u1", "=3B", [0, 128, 255]),
        ("int8", "=3b", [-128, 0, 127]),
        ("<i2", "<3h", [-32768, 0, 32767]),
        (">u2", ">3H", [0, 1, 65535]),
        ("=i4", "=3i", [-(2**31), 0, 2**31 - 1]),
        ("|u4", "=3I", [0, 1, 2**32 - 1]),
        ("int64", "=3q", [-(2**63), 0, 2**63 - 1]),
        (">u8", ">3Q", [0, 1, 2**64 - 1]),
        ("float16", "=3e", [-2.5, 0.0, 65504.0]),
        ("<f4", "<3f", [-2.5, 0.0, 1.125]),
        (">f8", ">3d", [-2.5, -0.0, 1e300]),
    ],
)
def test_portable_layouts_and_python_scalars_match_struct(
    dtype: str, fmt: str, values: list[float]
) -> None:
    expected = struct.pack(fmt, *values)
    blob = binpack.pack_array_portable(dtype, iter(values))
    assert type(blob) is bytes
    assert blob == expected
    actual = binpack.unpack_array_portable(dtype, blob)
    assert type(actual) is list
    assert actual == list(struct.unpack(fmt, expected))
    assert all(type(value) in (int, float) for value in actual)
    assert binpack.pack_array_portable(dtype, []) == b""
    assert binpack.unpack_array_portable(dtype, b"") == []


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize("dtype", ["u1", "<i4", ">u8"])
@pytest.mark.parametrize("value", [1.9, -1.0, float("inf"), float("nan"), "1", None])
def test_portable_integer_packing_rejects_coercion(dtype: str, value: object) -> None:
    with pytest.raises(struct.error):
        binpack.pack_array_portable(dtype, [value])


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        ("u1", -1),
        ("u1", 256),
        ("int8", -129),
        ("int8", 128),
        ("<u2", 65536),
        (">i4", 2**31),
        ("<i8", -(2**63) - 1),
        ("uint64", 2**64),
    ],
)
def test_portable_integer_packing_rejects_out_of_range_values(dtype: str, value: int):
    with pytest.raises(struct.error):
        binpack.pack_array_portable(dtype, [value])


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize(("dtype", "value"), [("f2", 65520.0), ("f4", 1e40)])
def test_portable_float_overflow_is_not_silently_infinity(dtype: str, value: float):
    with pytest.raises(OverflowError):
        binpack.pack_array_portable(dtype, [value])


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize("dtype", ["f2", "<f4", ">f8"])
def test_portable_floats_preserve_nonfinite_values_and_signed_zero(dtype: str):
    blob = binpack.pack_array_portable(dtype, [-0.0, math.inf, -math.inf, math.nan])
    values = binpack.unpack_array_portable(dtype, blob)
    assert math.copysign(1.0, values[0]) == -1.0
    assert values[1:3] == [math.inf, -math.inf]
    assert math.isnan(values[3])


@pytest.mark.usefixtures("array_backend")
def test_portable_numeric_protocol_and_snapshot_match_struct() -> None:
    source: list[object] = []

    class Indexed:
        def __index__(self) -> int:
            source.clear()
            return 7

    source.extend([Indexed(), True])
    assert binpack.pack_array_portable("<i4", source) == struct.pack("<2i", 7, 1)
    assert source == []

    class Floating:
        def __float__(self) -> float:
            return 1.5

    assert binpack.pack_array_portable(">f4", [Floating()]) == struct.pack(">f", 1.5)


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize("dtype", ["", "<", "i", "<c16", "?", "bool", "object", "S4"])
def test_portable_dtype_subset_is_enforced_with_or_without_numpy(dtype: str) -> None:
    with pytest.raises(struct.error, match="unsupported array dtype"):
        binpack.pack_array_portable(dtype, [1])
    with pytest.raises(struct.error, match="unsupported array dtype"):
        binpack.unpack_array_portable(dtype, bytes(16))


@pytest.mark.usefixtures("array_backend")
@pytest.mark.parametrize("shape", [(4,), (2, 2)], ids=["typed", "multidimensional"])
def test_portable_unpack_owns_values_and_uses_full_memoryview_storage(
    shape: tuple[int, ...],
) -> None:
    backing = bytearray(struct.pack("=4i", 1, -2, 300, 9999))
    source = memoryview(backing).cast("i", shape=shape)
    actual = binpack.unpack_array_portable("=i4", source)
    assert type(actual) is list
    assert actual == [1, -2, 300, 9999]
    backing[:] = bytes(len(backing))
    assert actual == [1, -2, 300, 9999]
    assert binpack.unpack_array_portable("=i4", memoryview(bytes(4)).cast("i", ())) == [
        0
    ]


@pytest.mark.usefixtures("array_backend")
def test_portable_unpack_rejects_partial_and_noncontiguous_buffers() -> None:
    with pytest.raises(struct.error, match="not a multiple of itemsize"):
        binpack.unpack_array_portable("i4", memoryview(bytes(6)).cast("H", (1, 3)))
    with pytest.raises(BufferError):
        binpack.unpack_array_portable("u1", memoryview(bytes(8))[::2])
    assert binpack.unpack_array_portable("<i4", struct.pack("<i", 7)) == [7]


@pytest.mark.skipif(
    not _HAVE_NUMPY, reason="needs NumPy to pin retained legacy coercion"
)
def test_legacy_numpy_coercion_and_zero_copy_results_remain_available() -> None:
    assert binpack.pack_array("u1", [1.9]) == b"\x01"
    with pytest.raises(struct.error):
        binpack.pack_array_portable("u1", [1.9])
    source = bytearray(b"\x01")
    legacy = binpack.unpack_array("u1", source)
    portable = binpack.unpack_array_portable("u1", source)
    source[0] = 2
    assert list(legacy) == [2]
    assert portable == [1]


@given(values=st.lists(st.integers(-(2**31), 2**31 - 1), max_size=80))
@pytest.mark.parametrize(("dtype", "order"), [("<i4", "<"), (">i4", ">")])
def test_portable_integer_arrays_have_struct_wire_parity(
    dtype: str, order: str, values: list[int]
) -> None:
    expected = struct.pack(f"{order}{len(values)}i", *values)
    assert binpack.pack_array_portable(dtype, values) == expected
    assert binpack.unpack_array_portable(dtype, expected) == values


def test_public_array_annotations_support_runtime_introspection() -> None:
    for name in (
        "pack_array",
        "unpack_array",
        "pack_array_portable",
        "unpack_array_portable",
    ):
        assert get_type_hints(getattr(binpack, name))
    assert get_type_hints(binpack.pack_array_portable)["dtype"] is str
    assert get_type_hints(binpack.unpack_array_portable)["dtype"] is str
    assert get_type_hints(binpack.unpack_array_portable)["return"] == list[float]
