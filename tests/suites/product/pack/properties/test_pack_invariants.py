"""Contract-backed properties for the stdlib-faithful binary packing surface."""

from __future__ import annotations

import struct

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.pack import binpack


_PREFIXES = st.sampled_from(("@", "=", "<", ">", "!"))
_FIELDS = st.lists(
    st.sampled_from(
        (
            ("?", st.booleans()),
            ("b", st.integers(-128, 127)),
            ("B", st.integers(0, 255)),
            ("h", st.integers(-(2**15), 2**15 - 1)),
            ("H", st.integers(0, 2**16 - 1)),
            ("i", st.integers(-(2**31), 2**31 - 1)),
            ("I", st.integers(0, 2**32 - 1)),
            ("q", st.integers(-(2**63), 2**63 - 1)),
            ("Q", st.integers(0, 2**64 - 1)),
        )
    ),
    min_size=1,
    max_size=8,
)


@st.composite
def _formats_and_values(draw: st.DrawFn) -> tuple[str, tuple[object, ...]]:
    prefix = draw(_PREFIXES)
    fields = draw(_FIELDS)
    return prefix + "".join(code for code, _ in fields), tuple(
        draw(values) for _, values in fields
    )


@given(case=_formats_and_values())
def test_scalar_surface_has_struct_byte_and_value_parity(
    case: tuple[str, tuple[object, ...]],
) -> None:
    """The stdlib wire format is the independent byte-level authority."""
    fmt, values = case
    expected = struct.pack(fmt, *values)

    assert binpack.calcsize(fmt) == struct.calcsize(fmt) == len(expected)
    assert binpack.pack(fmt, *values) == expected
    assert binpack.unpack(fmt, expected) == struct.unpack(fmt, expected)


@given(
    case=_formats_and_values(),
    prefix=st.binary(max_size=24),
    suffix=st.binary(max_size=24),
)
def test_pack_into_and_unpack_from_obey_struct_offsets(
    case: tuple[str, tuple[object, ...]],
    prefix: bytes,
    suffix: bytes,
) -> None:
    """Offsets alter only the stdlib-designated byte range."""
    fmt, values = case
    size = struct.calcsize(fmt)
    actual = bytearray(prefix + b"\xa5" * size + suffix)
    expected = actual.copy()
    offset = len(prefix)

    binpack.pack_into(fmt, actual, offset, *values)
    struct.pack_into(fmt, expected, offset, *values)

    assert actual == expected
    assert actual[:offset] == prefix
    assert actual[offset + size :] == suffix
    assert binpack.unpack_from(fmt, actual, offset) == struct.unpack_from(
        fmt, expected, offset
    )


@given(case=_formats_and_values(), data=st.data())
def test_unpack_rejects_every_truncated_struct(
    case: tuple[str, tuple[object, ...]],
    data: st.DataObject,
) -> None:
    """Any proper prefix is shorter than the format's authoritative size."""
    fmt, values = case
    blob = struct.pack(fmt, *values)
    cut = data.draw(st.integers(min_value=0, max_value=len(blob) - 1))
    truncated = blob[:cut]

    with pytest.raises(struct.error):
        binpack.unpack(fmt, truncated)
    with pytest.raises(struct.error):
        binpack.unpack_from(fmt, truncated)
