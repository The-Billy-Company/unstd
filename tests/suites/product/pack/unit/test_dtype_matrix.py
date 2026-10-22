"""Every declared array dtype, byte order and alias against hand-authored IEEE/two's-complement bytes.

Expected bytes are written out by hand in little-endian order (big-endian is the
reverse, native follows ``sys.byteorder``) and cross-checked with stdlib ``struct``.
Float rounding cases are IEEE 754 round-half-to-even ties computed on paper.
"""

from __future__ import annotations

import math
import struct
import sys
import warnings

import pytest

from tests.support.environment.backends import require_backend
from unstd.pack import binpack


# typestr -> (alias, struct char, [(value, little-endian hex)])
_ENDPOINTS: dict[str, tuple[str, str, list[tuple[float, str]]]] = {
    "i1": ("int8", "b", [(-128, "80"), (-1, "ff"), (127, "7f")]),
    "u1": ("uint8", "B", [(0, "00"), (255, "ff")]),
    "i2": ("int16", "h", [(-32768, "0080"), (-2, "feff"), (32767, "ff7f")]),
    "u2": ("uint16", "H", [(0, "0000"), (65535, "ffff")]),
    "i4": ("int32", "i", [(-(2**31), "00000080"), (2**31 - 1, "ffffff7f")]),
    "u4": ("uint32", "I", [(0, "00000000"), (2**32 - 1, "ffffffff")]),
    "i8": (
        "int64",
        "q",
        [(-(2**63), "0000000000000080"), (2**63 - 1, "ffffffffffffff7f")],
    ),
    "u8": ("uint64", "Q", [(0, "0000000000000000"), (2**64 - 1, "ffffffffffffffff")]),
    "f2": (
        "float16",
        "e",
        [
            (1.0, "003c"),
            (65504.0, "ff7b"),
            (-65504.0, "fffb"),
            (2.0**-14, "0004"),
            (2.0**-24, "0100"),
            (-0.0, "0080"),
            (math.inf, "007c"),
            (-math.inf, "00fc"),
        ],
    ),
    "f4": (
        "float32",
        "f",
        [
            (1.0, "0000803f"),
            (float.fromhex("0x1.fffffep127"), "ffff7f7f"),
            (2.0**-126, "00008000"),
            (2.0**-149, "01000000"),
            (-0.0, "00000080"),
            (math.inf, "0000807f"),
            (-math.inf, "000080ff"),
        ],
    ),
    "f8": (
        "float64",
        "d",
        [
            (1.0, "000000000000f03f"),
            (sys.float_info.max, "ffffffffffffef7f"),
            (sys.float_info.min, "0000000000001000"),
            (5e-324, "0100000000000000"),
            (-0.0, "0000000000000080"),
            (math.inf, "000000000000f07f"),
            (-math.inf, "000000000000f0ff"),
        ],
    ),
}
_NATIVE = "<" if sys.byteorder == "little" else ">"
_ORDERS = {"<": "<", ">": ">", "=": _NATIVE, "|": _NATIVE, "": _NATIVE}


def _wire(order: str, little_hex: str) -> bytes:
    raw = bytes.fromhex(little_hex)
    return raw if _ORDERS[order] == "<" else raw[::-1]


def _same(actual: list[float], expected: list[float]) -> bool:
    """Exact equality that also distinguishes -0.0 from 0.0."""
    return [(v, math.copysign(1, v)) for v in actual] == [
        (v, math.copysign(1, v)) for v in expected
    ]


@pytest.fixture(params=["fallback", "numpy"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "numpy":
        require_backend("numpy")
    else:
        monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
        monkeypatch.setattr(binpack, "np", None)
    return request.param


def test_scalar_surface_is_the_stdlib_struct_objects():
    for name in (
        "pack",
        "unpack",
        "unpack_from",
        "pack_into",
        "calcsize",
        "iter_unpack",
        "Struct",
        "error",
    ):
        assert getattr(binpack, name) is getattr(struct, name), name
    assert sorted(binpack.__all__) == sorted(
        [
            "Struct",
            "calcsize",
            "error",
            "iter_unpack",
            "pack",
            "pack_array",
            "pack_array_portable",
            "pack_into",
            "unpack",
            "unpack_array",
            "unpack_array_portable",
            "unpack_from",
        ]
    )


@pytest.mark.parametrize("order", list(_ORDERS), ids=lambda o: o or "bare")
@pytest.mark.parametrize("typestr", list(_ENDPOINTS))
def test_every_typestr_and_order_has_the_hand_written_layout(
    backend: str, typestr: str, order: str
):
    _, char, cases = _ENDPOINTS[typestr]
    values = [value for value, _ in cases]
    blob = b"".join(_wire(order, little) for _, little in cases)
    assert blob == struct.pack(f"{_ORDERS[order]}{len(values)}{char}", *values)

    dtype = order + typestr
    assert binpack.pack_array(dtype, values) == blob
    assert binpack.pack_array_portable(dtype, values) == blob
    assert _same(list(binpack.unpack_array(dtype, blob)), values)
    assert _same(binpack.unpack_array_portable(dtype, blob), values)


@pytest.mark.parametrize("typestr", list(_ENDPOINTS))
def test_spelled_out_aliases_are_native_order_typestrs(backend: str, typestr: str):
    alias, _, cases = _ENDPOINTS[typestr]
    values = [value for value, _ in cases]
    blob = b"".join(_wire("=", little) for _, little in cases)
    assert binpack.pack_array(alias, values) == blob
    assert binpack.pack_array_portable(alias, values) == blob
    assert _same(binpack.unpack_array_portable(alias, blob), values)
    assert _same(list(binpack.unpack_array(alias, blob)), values)


_INTEGER_LIMITS = {
    "i1": (-(2**7), 2**7 - 1),
    "u1": (0, 2**8 - 1),
    "i2": (-(2**15), 2**15 - 1),
    "u2": (0, 2**16 - 1),
    "i4": (-(2**31), 2**31 - 1),
    "u4": (0, 2**32 - 1),
    "i8": (-(2**63), 2**63 - 1),
    "u8": (0, 2**64 - 1),
}


@pytest.mark.parametrize("typestr", list(_INTEGER_LIMITS))
def test_one_past_each_integer_limit_is_rejected(backend: str, typestr: str):
    low, high = _INTEGER_LIMITS[typestr]
    numpy_error = OverflowError
    for value in (low - 1, high + 1):
        with pytest.raises(binpack.error):
            binpack.pack_array_portable("<" + typestr, [value])
        with pytest.raises(binpack.error if backend == "fallback" else numpy_error):
            binpack.pack_array("<" + typestr, [value])
    # A rejected element leaves the helpers usable for the in-range neighbours.
    assert binpack.pack_array_portable("<" + typestr, [low, high]) == struct.pack(
        "<2" + _ENDPOINTS[typestr][1], low, high
    )


# value -> little-endian bytes after IEEE round-half-to-even.
_ROUNDING = {
    "f2": [
        (1 + 2**-11, "003c"),  # tie between 1.0 (even) and 1+2**-10
        (1 + 3 * 2**-11, "023c"),  # tie between 1+2**-10 and 1+2**-9 (even)
        (1 + 2**-11 + 2**-30, "013c"),  # just above the tie rounds up
        (2.0**-25, "0000"),  # half the smallest subnormal ties to zero
        (3 * 2.0**-25, "0200"),  # 1.5 subnormal steps ties to the even 2
        (65519.0, "ff7b"),  # below the overflow tie stays at max finite
        (-(2.0**-26), "0080"),  # underflow keeps the sign
    ],
    "f4": [
        (1 + 2**-24, "0000803f"),
        (1 + 3 * 2**-24, "0200803f"),
        (2.0**-150, "00000000"),
        (3 * 2.0**-150, "02000000"),
        (-(2.0**-151), "00000080"),
    ],
}


@pytest.mark.parametrize("typestr", list(_ROUNDING))
def test_narrow_floats_round_half_to_even(backend: str, typestr: str):
    for value, little in _ROUNDING[typestr]:
        expected = bytes.fromhex(little)
        assert binpack.pack_array_portable("<" + typestr, [value]) == expected, value
        assert binpack.pack_array("<" + typestr, [value]) == expected, value


@pytest.mark.parametrize(
    ("typestr", "value"),
    [("f2", 65520.0), ("f2", -65520.0), ("f4", float.fromhex("0x1.ffffffp127"))],
)
def test_overflow_tie_is_an_error_or_backend_infinity(
    backend: str, typestr: str, value: float
):
    """The tie above max finite rounds to infinity: struct refuses, NumPy warns."""
    with pytest.raises(OverflowError):
        binpack.pack_array_portable("<" + typestr, [value])
    if backend == "fallback":
        with pytest.raises(OverflowError):
            binpack.pack_array("<" + typestr, [value])
        return
    infinity = {"f2": "007c", "f4": "0000807f"}[typestr]
    signed = bytearray.fromhex(infinity)
    signed[-1] |= 0x80 if value < 0 else 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        assert binpack.pack_array("<" + typestr, [value]) == bytes(signed)


# Quiet NaNs with payloads and a signalling NaN, little-endian.
_NANS = {
    "f2": ["007e", "017e", "00fe", "017c"],
    "f4": ["0000c07f", "0100c07f", "0000c0ff", "0100807f"],
    "f8": [
        "000000000000f87f",
        "010000000000f87f",
        "000000000000f8ff",
        "010000000000f07f",
    ],
}


@pytest.mark.parametrize("typestr", list(_NANS))
def test_nan_bit_patterns_decode_as_nan_and_repack_like_struct(typestr: str):
    char = _ENDPOINTS[typestr][1]
    for little in _NANS[typestr]:
        raw = bytes.fromhex(little)
        (value,) = binpack.unpack_array_portable("<" + typestr, raw)
        assert math.isnan(value)
        assert math.copysign(1, value) == (-1 if raw[-1] & 0x80 else 1)
        assert binpack.pack_array_portable("<" + typestr, [value]) == struct.pack(
            "<" + char, value
        )
    # A Python float is a double, so every float64 payload survives exactly.
    if typestr == "f8":
        for little in _NANS["f8"]:
            raw = bytes.fromhex(little)
            assert (
                binpack.pack_array_portable(
                    "<f8", binpack.unpack_array_portable("<f8", raw)
                )
                == raw
            )


@pytest.mark.parametrize("typestr", list(_NANS))
def test_numpy_view_and_repack_preserve_every_nan_bit(typestr: str):
    require_backend("numpy")
    raw = b"".join(bytes.fromhex(little) for little in _NANS[typestr])
    view = binpack.unpack_array("<" + typestr, raw)
    assert view.tobytes() == raw
    assert all(math.isnan(value) for value in view.tolist())
    assert binpack.pack_array("<" + typestr, view) == raw


@pytest.mark.parametrize(
    ("itemsize", "extra"), [(2, 1), (4, 1), (4, 3), (8, 1), (8, 3), (8, 7)]
)
def test_partial_trailing_bytes_are_rejected_with_their_byte_count(
    backend: str, itemsize: int, extra: int
):
    blob = bytes(2 * itemsize + extra)
    dtype = f"<u{itemsize}"
    message = rf"^unstd\.pack: buffer size {len(blob)} is not a multiple of itemsize {itemsize}$"
    with pytest.raises(binpack.error, match=message):
        binpack.unpack_array_portable(dtype, blob)
    with pytest.raises(binpack.error if backend == "fallback" else ValueError):
        binpack.unpack_array(dtype, blob)
    assert list(binpack.unpack_array(dtype, blob[: 2 * itemsize])) == [0, 0]


@pytest.mark.parametrize(
    "dtype",
    ["<i3", "f16", "u16", "i0", "f1", "<<i4", "I4", "F8", "float", "int", "b1", "c8"],
)
def test_malformed_dtypes_are_rejected_by_both_portable_helpers(dtype: str):
    with pytest.raises(binpack.error, match="unsupported array dtype"):
        binpack.pack_array_portable(dtype, [])
    with pytest.raises(binpack.error, match="unsupported array dtype"):
        binpack.unpack_array_portable(dtype, b"")
