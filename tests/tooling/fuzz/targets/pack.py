"""Independent struct oracle for the bulk codecs, including native NumPy paths.

Envelope: byte 0 modulo 3 selects generated encode, arbitrary decode, or named
dtype aliases; byte 1 selects one of eleven numeric kinds and four byte orders.
The remaining bytes supply full-width integers or finite fractional samples.
Empty/missing header bytes mean zero. Arbitrary decode always exercises an
aligned prefix and, for multi-byte kinds, a partial trailing element refusal.
Both sized and iterator encode paths and bytes/bytearray/memoryview decode paths
are checked. No optional backend is required.
"""

from __future__ import annotations

import math
import struct

from unstd.pack import binpack


_KINDS = (
    ("i1", "b", "int8"),
    ("u1", "B", "uint8"),
    ("i2", "h", "int16"),
    ("u2", "H", "uint16"),
    ("i4", "i", "int32"),
    ("u4", "I", "uint32"),
    ("i8", "q", "int64"),
    ("u8", "Q", "uint64"),
    ("f2", "e", "float16"),
    ("f4", "f", "float32"),
    ("f8", "d", "float64"),
)


def _equal(actual, expected):
    actual = list(actual)
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected, strict=True):
        if isinstance(want, float) and math.isnan(want):
            assert math.isnan(got)
        else:
            assert got == want
            if want == 0 and isinstance(want, float):
                assert math.copysign(1.0, got) == math.copysign(1.0, want)


def fuzz(data: bytes) -> None:
    """Exercise actual array conversion against independently packed values."""
    mode = data[0] % 3 if data else 0
    selector = data[1] if len(data) > 1 else 0
    payload = data[2:]
    kind, char, alias = _KINDS[selector % len(_KINDS)]
    byteorder = ("<", ">", "=", "|")[(selector // len(_KINDS)) % 4]
    order = "=" if byteorder == "|" else byteorder
    dtype = alias if mode == 2 else byteorder + kind
    if mode == 2:
        order = "="
    size = int(kind[1:])
    if mode == 1:
        raw = payload[: len(payload) // size * size]
        expected = struct.unpack(f"{order}{len(raw) // size}{char}", raw)
    else:
        values = []
        for offset in range(0, len(payload), size):
            chunk = payload[offset : offset + size].ljust(size, b"\0")
            if kind[0] == "f":
                values.append(int.from_bytes(chunk[:2], "little", signed=True) / 8)
            else:
                values.append(int.from_bytes(chunk, "little", signed=kind[0] == "i"))
        expected_raw = struct.pack(f"{order}{len(values)}{char}", *values)
        assert binpack.pack_array(dtype, values) == expected_raw
        assert binpack.pack_array(dtype, iter(values)) == expected_raw
        raw = expected_raw
        expected = struct.unpack(f"{order}{len(values)}{char}", raw)
    for buffer in (raw, bytearray(raw), memoryview(raw)):
        _equal(binpack.unpack_array(dtype, buffer), expected)
    if size > 1:
        try:
            binpack.unpack_array(dtype, raw + b"\xff")
        except (ValueError, struct.error):
            pass
        else:
            msg = "partial trailing numeric element was accepted"
            raise AssertionError(msg)
