"""The private native entry point rejects unsafe raw metadata before scanning."""

from __future__ import annotations

import struct
import sys

import pytest


@pytest.fixture
def native():
    return pytest.importorskip("unstd.pack._pack_native", exc_type=ImportError)


_FRAME = struct.pack("<2q2f", 0, 2, 1.0, 2.0)
_ARGUMENTS = (_FRAME, 2, 1, "f", "<", "q", "<", True, True)


class _BytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    ("position", "value", "exception"),
    [
        pytest.param(0, bytearray(_FRAME), TypeError, id="mutable-storage"),
        pytest.param(0, memoryview(_FRAME), TypeError, id="view-storage"),
        pytest.param(0, _BytesSubclass(_FRAME), TypeError, id="subclass-storage"),
        pytest.param(1, 0, ValueError, id="zero-count"),
        pytest.param(1, -1, ValueError, id="negative-count"),
        pytest.param(1, 10**5000, ValueError, id="oversized-count"),
        pytest.param(2, 0, ValueError, id="zero-columns"),
        pytest.param(2, -1, ValueError, id="negative-columns"),
        pytest.param(2, sys.maxsize, ValueError, id="row-width-overflow"),
        pytest.param(1, 1.5, TypeError, id="fractional-count"),
        pytest.param(2, "1", TypeError, id="string-columns"),
        pytest.param(3, "x", ValueError, id="unsupported-value-code"),
        pytest.param(3, "ff", ValueError, id="multiple-value-codes"),
        pytest.param(3, "é", ValueError, id="non-ascii-value-code"),
        pytest.param(3, "\x00", ValueError, id="nul-value-code"),
        pytest.param(5, "f", ValueError, id="floating-index-code"),
        pytest.param(4, "!", ValueError, id="unsupported-value-order"),
        pytest.param(6, "@", ValueError, id="unsupported-index-order"),
        pytest.param(3, None, TypeError, id="non-string-value-code"),
        pytest.param(4, b"<", TypeError, id="non-string-value-order"),
        pytest.param(7, 1, TypeError, id="non-bool-finite"),
        pytest.param(8, 0, TypeError, id="non-bool-empty"),
    ],
)
def test_raw_arguments_cannot_bypass_storage_and_metadata_guards(
    native, position, value, exception
):
    arguments = list(_ARGUMENTS)
    arguments[position] = value
    with pytest.raises(exception):
        native.validate(*arguments)


@pytest.mark.parametrize("delta", [-1, 1])
def test_argument_count_is_checked(native, delta):
    arguments = _ARGUMENTS[:-1] if delta < 0 else (*_ARGUMENTS, None)
    with pytest.raises(TypeError):
        native.validate(*arguments)


def test_partial_rows_are_refused_before_index_or_value_scans(native):
    arguments = list(_ARGUMENTS)
    arguments[0] += struct.pack("<f", 3.0)
    arguments[2] = 2
    with pytest.raises(ValueError, match="partial matrix row"):
        native.validate(*arguments)


@pytest.mark.parametrize(
    ("char", "size", "order", "pattern"),
    [
        ("e", 2, ">", 0x7C01),
        ("f", 4, "<", 0x7FC00001),
        ("d", 8, ">", 0x7FF0000000000000),
    ],
)
def test_last_unaligned_float_word_is_checked_and_finite_flag_is_respected(
    native, char, size, order, pattern
):
    source = (
        b"\x00\x11"
        + bytes(16 * size)
        + pattern.to_bytes(size, "little" if order == "<" else "big")
    )
    arguments = (source, 2, 1, char, order, "B", "=", True, True)
    with pytest.raises(ValueError, match="nonfinite"):
        native.validate(*arguments)
    assert native.validate(*arguments[:7], False, True) == 17


def test_integer_index_protocol_is_admitted_and_returns_python_row_count(native):
    class Indexed:
        def __init__(self, value):
            self.value = value

        def __index__(self):
            return self.value

    result = native.validate(
        _FRAME, Indexed(2), Indexed(1), "f", "<", "q", "<", True, True
    )
    assert type(result) is int
    assert result == 2
    assert type(native.KERNEL_VERSION) is int
    assert native.KERNEL_VERSION > 0


def test_failure_does_not_poison_a_later_frame(native):
    assert native.validate(*_ARGUMENTS) == 2
    bad = struct.pack(">2Q2f", 0, 2**64 - 1, 1.0, 2.0)
    with pytest.raises(ValueError, match="indices"):
        native.validate(bad, 2, 1, "f", ">", "Q", ">", True, True)
    assert native.validate(*_ARGUMENTS) == 2
