"""Array byte windows preserve struct values, owned results and NumPy views."""

from __future__ import annotations

from array import array
import gc
import struct

import pytest

from tests.support.environment.backends import require_backend
from unstd.pack import binpack


@pytest.fixture(params=["portable", "fallback"])
def decode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.param == "fallback":
        monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
        monkeypatch.setattr(binpack, "np", None)
        return binpack.unpack_array
    return binpack.unpack_array_portable


_LAYOUTS = [
    ("u1", "=B", [0, 127, 255]),
    ("<i4", "<i", [-(2**31), 7, 2**31 - 1]),
    (">u2", ">H", [0, 17, 65535]),
    ("float16", "=e", [-0.0, -1.5, 65504.0]),
    ("<f4", "<f", [-0.0, 1.25, -3.5]),
    (">f8", ">d", [-0.0, 1e-300, 1e300]),
]


@pytest.mark.parametrize(("dtype", "fmt", "values"), _LAYOUTS)
def test_explicit_count_decodes_unaligned_window_and_ignores_trailing_bytes(
    decode, dtype, fmt, values
):
    payload = struct.pack(f"{fmt[0]}{len(values)}{fmt[1]}", *values)
    source = b"\xa5" + payload + b"\xff"
    actual = decode(dtype, source, count=len(values), offset=1)
    assert type(actual) is list
    assert actual == list(struct.unpack(f"{fmt[0]}{len(values)}{fmt[1]}", payload))


@pytest.mark.parametrize(("dtype", "fmt", "values"), _LAYOUTS)
def test_default_count_consumes_all_remaining_whole_elements(
    decode, dtype, fmt, values
):
    payload = struct.pack(f"{fmt[0]}{len(values)}{fmt[1]}", *values)
    source = b"\x99\xaa\xbb" + payload
    assert decode(dtype, source, offset=3) == list(
        struct.unpack(f"{fmt[0]}{len(values)}{fmt[1]}", payload)
    )


@pytest.mark.parametrize("exporter", ["array", "typed", "matrix", "scalar"])
def test_windows_use_exporter_storage_bytes_independent_of_shape(decode, exporter):
    values = [0x11223344, 0x01020304, 17, 99]
    backing = bytearray(struct.pack("=4I", *values))
    if exporter == "array":
        source = array("I", values)
    elif exporter == "typed":
        source = memoryview(backing).cast("I")
    elif exporter == "matrix":
        source = memoryview(backing).cast("I", (2, 2))
    else:
        source = memoryview(backing)[:4].cast("I", ())
    if exporter == "scalar":
        assert decode("u1", source, count=2, offset=1) == list(backing[1:3])
    else:
        assert decode("=u4", source, count=2, offset=4) == [values[1], values[2]]


def test_count_and_offset_accept_integer_index_protocol(decode):
    class Indexed:
        def __init__(self, value):
            self.value = value

        def __index__(self):
            return self.value

    source = b"\xaa" + struct.pack(">3h", -1, 2, 3)
    assert decode(">i2", source, count=Indexed(2), offset=Indexed(3)) == [2, 3]


@pytest.mark.parametrize(
    ("count", "offset", "exception"),
    [
        (-2, 0, ValueError),
        (1.5, 0, TypeError),
        (1, 0.5, TypeError),
        (1, -1, binpack.error),
        (0, 9, binpack.error),
        (3, 1, binpack.error),
        (2**70, 0, binpack.error),
        pytest.param(10**5000, 0, binpack.error, id="oversized-count"),
        pytest.param(0, 10**5000, binpack.error, id="oversized-offset"),
        pytest.param(0, -(10**5000), binpack.error, id="oversized-negative-offset"),
    ],
)
def test_invalid_range_values_raise_without_truncation(
    decode, count, offset, exception
):
    with pytest.raises(exception):
        decode("<i4", bytes(8), count=count, offset=offset)


def test_dtype_is_validated_before_range_index_protocols(decode):
    class UnreadableRange:
        def __index__(self):
            pytest.fail("a malformed dtype reached range conversion")

    with pytest.raises(binpack.error, match="unsupported array dtype"):
        decode("invalid", b"", count=UnreadableRange(), offset=UnreadableRange())


def test_zero_count_and_empty_remaining_window_return_owned_empty_list(decode):
    assert decode("i4", b"abc", count=0, offset=3) == []
    assert decode("i4", b"abc", offset=3) == []
    assert decode("i4", b"abc", count=0, offset=1) == []


def test_implicit_count_rejects_a_partial_remaining_element(decode):
    raw = b"\xaa" + struct.pack("<i", 7) + b"\xff"
    with pytest.raises(binpack.error):
        decode("<i4", raw, offset=1)
    assert decode("<i4", raw, count=1, offset=1) == [7]


@pytest.mark.parametrize("base", [bytes, bytearray])
def test_range_decode_does_not_slice_buffer_subclasses(decode, base):
    class Unsliceable(base):
        def __getitem__(self, key):
            if isinstance(key, slice):
                pytest.fail("range decode sliced its source")
            return super().__getitem__(key)

    source = Unsliceable(b"prefix" + struct.pack(">2i", -7, 99) + b"tail")
    assert decode(">i4", source, count=2, offset=6) == [-7, 99]


def test_owned_results_and_failure_paths_release_buffer_exports(decode):
    source = bytearray(b"\xaa" + struct.pack("<2i", 7, 8))
    decoded = decode("<i4", source, count=2, offset=1)
    source.extend(b"\xff")
    source[1:5] = struct.pack("<i", -1)
    assert decoded == [7, 8]
    with pytest.raises(binpack.error):
        decode("<i4", source, count=3, offset=1)
    source.extend(b"\x00")


def test_selected_ranges_still_require_a_contiguous_exporter(decode):
    source = memoryview(bytes(16))[::2]
    with pytest.raises(BufferError):
        decode("u1", source, count=1, offset=0)


def test_numpy_selected_view_aliases_the_original_unaligned_window_and_keeps_it_alive():
    require_backend("numpy")
    source = bytearray(b"\xaa" + struct.pack("<4i", 1, 2, 3, 4) + b"tail")
    result = binpack.unpack_array("<i4", source, count=2, offset=5)
    assert result.tolist() == [2, 3]
    assert result.flags.writeable
    assert not result.flags.owndata
    result[1] = -7
    assert struct.unpack_from("<4i", source, 1) == (1, 2, -7, 4)
    with pytest.raises(BufferError):
        source.extend(b"\x00")
    del source
    gc.collect()
    assert result.tolist() == [2, -7]


def test_numpy_readonly_selected_view_retains_broader_dtype_support():
    np = require_backend("numpy")
    dtype = np.dtype(">c16")
    source = b"\xaa" + struct.pack(">4d", 1.5, -2.5, -3.0, 4.0)
    result = binpack.unpack_array(dtype, source, count=1, offset=17)
    assert result.dtype == dtype
    assert result.tolist() == [complex(-3.0, 4.0)]
    assert not result.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        result[0] = 0


@pytest.mark.parametrize(
    ("count", "offset"),
    [(2, 1), (-1, 1), (0, 13), (-2, 1), (1, -1), (1, 14), (2**70, 0)],
)
def test_numpy_range_validation_follows_the_selected_backend(count, offset):
    np = require_backend("numpy")
    source = b"\xaa" + struct.pack("<3i", 1, 2, 3)
    try:
        expected = np.frombuffer(source, dtype="<i4", count=count, offset=offset)
    except (ValueError, TypeError, OverflowError) as reference:
        with pytest.raises(type(reference)):
            binpack.unpack_array("<i4", source, count=count, offset=offset)
    else:
        actual = binpack.unpack_array("<i4", source, count=count, offset=offset)
        assert actual.dtype == expected.dtype
        assert actual.shape == expected.shape
        assert actual.tobytes() == expected.tobytes()
