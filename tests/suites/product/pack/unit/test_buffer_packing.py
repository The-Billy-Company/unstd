"""Primitive array snapshots retain struct bytes, errors and numeric protocols."""

from __future__ import annotations

from array import array
import struct
import sys

import pytest

from tests.support.environment.backends import require_backend
from unstd.pack import binpack


_FORMATS = {
    "i1": ("b", [-128, 0, 127]),
    "u1": ("B", [0, 128, 255]),
    "i2": ("h", [-32768, 0, 32767]),
    "u2": ("H", [0, 32768, 65535]),
    "i4": ("i", [-(2**31), 0, 2**31 - 1]),
    "u4": ("I", [0, 2**31, 2**32 - 1]),
    "i8": ("q", [-(2**63), 0, 2**63 - 1]),
    "u8": ("Q", [0, 2**63, 2**64 - 1]),
    "f8": ("d", [-0.0, 5e-324, -1.5, float("inf")]),
}


@pytest.fixture(params=["portable", "fallback"])
def pack_array(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.param == "fallback":
        monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
        monkeypatch.setattr(binpack, "np", None)
        return binpack.pack_array
    return binpack.pack_array_portable


@pytest.mark.parametrize("typecode", list("bBhHiIlLqQd"))
@pytest.mark.parametrize("order", ["<", ">", "=", "|"])
def test_native_arrays_pack_exact_struct_values_and_release_exports(
    pack_array, typecode: str, order: str
):
    source = array(typecode)
    kind = "f" if typecode == "d" else "u" if typecode.isupper() else "i"
    dtype = f"{kind}{source.itemsize}"
    char, values = _FORMATS[dtype]
    source.extend(values)
    expected = struct.pack(
        f"{'=' if order == '|' else order}{len(values)}{char}", *values
    )
    actual = pack_array(order + dtype, source)
    assert type(actual) is bytes
    assert actual == expected
    source.append(0)
    source[0] = 0
    assert actual == expected


@pytest.mark.parametrize("typestr", list(_FORMATS))
@pytest.mark.parametrize("source_order", ["<", ">"])
@pytest.mark.parametrize("target_order", ["<", ">", "=", "|"])
@pytest.mark.parametrize("step", [1, 2, -1])
def test_ndarrays_pack_logical_one_dimensional_values_in_target_order(
    pack_array, typestr: str, source_order: str, target_order: str, step: int
):
    np = require_backend("numpy")
    char, values = _FORMATS[typestr]
    source = np.array(values, dtype=source_order + typestr)[::step]
    snapshot = tuple(source)
    expected = struct.pack(
        f"{'=' if target_order == '|' else target_order}{len(snapshot)}{char}",
        *snapshot,
    )
    actual = pack_array(target_order + typestr, source)
    assert actual == expected
    source[:] = 0
    assert actual == expected


@pytest.mark.parametrize("typestr", ["f2", "f4", "f8"])
@pytest.mark.parametrize("source_order", ["<", ">"])
@pytest.mark.parametrize("target_order", ["<", ">"])
def test_ndarray_nan_sign_payload_and_quieting_match_struct(
    pack_array, typestr: str, source_order: str, target_order: str
):
    np = require_backend("numpy")
    size = int(typestr[1:])
    patterns = {
        "f2": [0x7C01, 0x7E01, 0xFC01],
        "f4": [0x7F800001, 0x7FC00001, 0xFF800001],
        "f8": [0x7FF0000000000001, 0x7FF8000000000001, 0xFFF0000000000001],
    }[typestr]
    raw = b"".join(
        value.to_bytes(size, "little" if source_order == "<" else "big")
        for value in patterns
    )
    source = np.frombuffer(raw, dtype=source_order + typestr)
    char = {"f2": "e", "f4": "f", "f8": "d"}[typestr]
    expected = struct.pack(f"{target_order}{len(patterns)}{char}", *tuple(source))
    assert pack_array(target_order + typestr, source) == expected


def test_array_double_nan_bits_match_struct_for_both_byte_orders(pack_array):
    source = array("d")
    raw = b"".join(
        value.to_bytes(8, sys.byteorder)
        for value in [0x7FF0000000000001, 0x7FF8000000000001, 0xFFF0000000000001]
    )
    source.frombytes(raw)
    for order in ("<", ">"):
        assert pack_array(order + "f8", source) == struct.pack(
            f"{order}3d", *tuple(source)
        )


def test_array_subclasses_keep_overridden_iteration(pack_array):
    class CustomArray(array):
        def __iter__(self):
            return iter([7, 8])

    source = CustomArray("i", [1, 2, 3])
    assert pack_array("<i4", source) == struct.pack("<2i", 7, 8)


def test_ndarray_subclasses_keep_overridden_iteration(pack_array):
    np = require_backend("numpy")

    class CustomArray(np.ndarray):
        def __iter__(self):
            return iter([7, 8])

    source = np.array([1, 2, 3], dtype="i4").view(CustomArray)
    assert pack_array("<i4", source) == struct.pack("<2i", 7, 8)


@pytest.mark.parametrize("shape", [(), (2, 2)])
def test_ndarray_dimensions_keep_iterable_errors(pack_array, shape: tuple[int, ...]):
    np = require_backend("numpy")
    source = np.zeros(shape, dtype="i4")
    with pytest.raises((TypeError, struct.error)) as reference:
        struct.pack("<2i", *tuple(source))
    with pytest.raises(type(reference.value)):
        pack_array("<i4", source)


@pytest.mark.parametrize(
    ("dtype", "typecode", "value"),
    [("u4", "i", -1), ("i4", "I", 2**32 - 1), ("i4", "d", 1.5), ("f4", "d", 1e40)],
)
def test_typed_arrays_keep_struct_range_and_conversion_errors(
    pack_array, dtype: str, typecode: str, value: float
):
    with pytest.raises(OverflowError if dtype == "f4" else struct.error):
        pack_array(dtype, array(typecode, [value]))


def test_typed_arrays_keep_numeric_conversion_when_itemsize_changes(pack_array):
    source = array("i", [1, -2, 3])
    assert pack_array(">i8", source) == struct.pack(">3q", 1, -2, 3)
    assert pack_array("<f8", source) == struct.pack("<3d", 1, -2, 3)


@pytest.mark.parametrize("dtype", ["datetime64[ns]", "timedelta64[ns]"])
def test_ndarrays_without_buffer_formats_keep_struct_conversion(pack_array, dtype):
    np = require_backend("numpy")
    source = np.array([1, 2], dtype=dtype)
    assert pack_array("<f8", source) == struct.pack("<2d", *tuple(source))
    with pytest.raises(struct.error):
        pack_array("<i8", source)


def test_empty_arrays_match_struct_for_both_orders(pack_array):
    for order in ("<", ">"):
        assert pack_array(order + "i4", array("i")) == b""
        assert pack_array(order + "f8", array("d")) == b""
