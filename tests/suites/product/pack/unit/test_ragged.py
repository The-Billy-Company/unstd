"""Numeric frame admission preserves independent struct values and immutable ownership."""

from __future__ import annotations

import math
import struct
import sys
from typing import get_type_hints

import pytest

from tests.support.environment.backends import require_backend
from unstd.pack import ragged


@pytest.fixture(params=["python", "native"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.param == "python":
        monkeypatch.setattr(ragged, "_native", None)
    elif ragged._native is None:
        pytest.skip("native pack validator is not built")


def _wire(values, offsets, *, value="<f", index="<q"):
    return struct.pack(f"{index[0]}{len(offsets)}{index[1]}", *offsets) + struct.pack(
        f"{value[0]}{len(values)}{value[1]}", *values
    )


def _rows(values):
    return tuple(tuple(row) for row in values)


def _owner(values):
    while getattr(values, "base", None) is not None:
        values = values.base
    return values


_LAYOUTS = [
    ("i1", "=b", [-128, 0, 127, -3, 8, 9], "u1", "=B"),
    (">u4", ">I", [0, 2**32 - 1, 1, 2, 3, 4], ">u2", ">H"),
    ("float16", "=e", [-0.0, 2**-24, 65504.0, -2.5, 3.0, 4.0], "u1", "=B"),
    (">f4", ">f", [-0.0, 2**-149, -1.25, 2.5, 3.0, 4.0], "u1", "=B"),
    ("<f8", "<d", [-0.0, 5e-324, -1e300, 1e300, 3.0, 4.0], "u1", "=B"),
    ("=i8", "=q", [-(2**63), 2**63 - 1, 0, -1, 2, 3], "int64", "=q"),
]


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize(
    ("dtype", "fmt", "values", "index_dtype", "index_fmt"), _LAYOUTS
)
def test_struct_layouts_include_foreign_order_and_unaligned_matrix_bytes(
    dtype, fmt, values, index_dtype, index_fmt
):
    source = _wire(values, [0, 1, 3], value=fmt, index=index_fmt)
    decoder = ragged.RaggedDecoder(
        dtype, columns=2, index_dtype=index_dtype, finite=True, allow_empty=False
    )
    matrix, offsets = decoder.unpack(source, index_count=3)
    expected = struct.unpack(
        f"{fmt[0]}6{fmt[1]}", struct.pack(f"{fmt[0]}6{fmt[1]}", *values)
    )
    assert _rows(matrix) == tuple(tuple(expected[i : i + 2]) for i in (0, 2, 4))
    assert tuple(offsets) == (0, 1, 3)
    assert math.copysign(1.0, float(matrix[0][0])) == math.copysign(
        1.0, float(expected[0])
    )


@pytest.mark.usefixtures("backend")
def test_all_integer_widths_retain_signedness_and_index_byte_order():
    integers = [
        ("i1", "b", -(2**7), 2**7 - 1),
        ("u1", "B", 0, 2**8 - 1),
        ("i2", "h", -(2**15), 2**15 - 1),
        ("u2", "H", 0, 2**16 - 1),
        ("i4", "i", -(2**31), 2**31 - 1),
        ("u4", "I", 0, 2**32 - 1),
        ("i8", "q", -(2**63), 2**63 - 1),
        ("u8", "Q", 0, 2**64 - 1),
    ]
    for kind, char, low, high in integers:
        for order in ("<", ">"):
            source = _wire(
                [low, high], [0, 1, 2], value=order + char, index=order + char
            )
            matrix, offsets = ragged.RaggedDecoder(
                order + kind, columns=1, index_dtype=order + kind
            ).unpack(source, index_count=3)
            assert _rows(matrix) == ((low,), (high,))
            assert tuple(offsets) == (0, 1, 2)


_BAD_FRAMES = [
    pytest.param([1, 2], [1.0, 2.0], "<q", "<i8", 2, id="nonzero-first"),
    pytest.param([0, 2, 1, 3], [1.0, 2.0, 3.0], "<q", "<i8", 4, id="later-decrease"),
    pytest.param([0, 3], [1.0, 2.0], "<q", "<i8", 2, id="terminal-too-large"),
    pytest.param([0, 1], [1.0, 2.0], "<q", "<i8", 2, id="terminal-too-small"),
    pytest.param([0, -1, 2], [1.0, 2.0], "<q", "<i8", 3, id="negative-boundary"),
    pytest.param([0, 2**64 - 1], [1.0, 2.0], ">Q", ">u8", 2, id="unsigned-terminal"),
    pytest.param([0], [], "<q", "<i8", 2, id="truncated-header"),
]


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize(
    ("offsets", "values", "index_fmt", "index_dtype", "count"), _BAD_FRAMES
)
def test_invalid_boundary_tables_fail_before_output(
    offsets, values, index_fmt, index_dtype, count
):
    source = _wire(values, offsets, index=index_fmt)
    with pytest.raises(
        ValueError, match=r"boundar|indic|index|header|frame|offset|increasing"
    ):
        ragged.RaggedDecoder("<f4", columns=1, index_dtype=index_dtype).unpack(
            source, index_count=count
        )


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("tail", [b"x", struct.pack("<f", 3.0)])
def test_payload_requires_whole_numeric_rows(tail):
    source = _wire([1.0, 2.0], [0, 1]) + tail
    with pytest.raises(ValueError, match=r"row|size|payload|frame"):
        ragged.RaggedDecoder("<f4", columns=2).unpack(source, index_count=2)


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize(
    ("kind", "size", "bad"),
    [
        ("f2", 2, [0x7C00, 0xFC00, 0x7C01, 0xFE01]),
        ("f4", 4, [0x7F800000, 0xFF800000, 0x7F800001, 0xFFC00001]),
        (
            "f8",
            8,
            [
                0x7FF0000000000000,
                0xFFF0000000000000,
                0x7FF0000000000001,
                0xFFF8000000000001,
            ],
        ),
    ],
)
def test_finite_checks_reach_the_final_word_for_both_endian_orders(kind, size, bad):
    # Six boundary bytes make the float32/float64 payloads unaligned; the final
    # word also follows the native scan's normal vector and checkpoint chunks.
    count = 16387
    for order, byteorder in (("<", "little"), (">", "big")):
        decoder = ragged.RaggedDecoder(
            order + kind, columns=1, index_dtype=">u2", finite=True
        )
        header = struct.pack(">3H", 0, 1, count)
        valid = bytes(size * (count - 1))
        for pattern in bad:
            source = header + valid + pattern.to_bytes(size, byteorder)
            with pytest.raises(ValueError, match=r"finite|NaN|inf"):
                decoder.unpack(source, index_count=3)


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize(("dtype", "char"), [("<f2", "e"), (">f4", "f"), ("=f8", "d")])
def test_nonfinite_values_are_preserved_when_the_constraint_is_disabled(dtype, char):
    values = [-0.0, math.inf, -math.inf, math.nan]
    matrix, offsets = ragged.RaggedDecoder(dtype, columns=2, finite=False).unpack(
        _wire(values, [0, 2], value=dtype[0] + char), index_count=2
    )
    assert math.copysign(1.0, float(matrix[0][0])) == -1.0
    assert float(matrix[0][1]) == math.inf
    assert float(matrix[1][0]) == -math.inf
    assert math.isnan(float(matrix[1][1]))
    assert tuple(offsets) == (0, 2)


@pytest.mark.usefixtures("backend")
def test_empty_segments_and_empty_frame_follow_the_declared_constraint():
    source = _wire([1.0, 2.0, 3.0], [0, 0, 2, 2, 3])
    values, offsets = ragged.RaggedDecoder("<f4", columns=1).unpack(
        source, index_count=5
    )
    assert _rows(values) == ((1.0,), (2.0,), (3.0,))
    assert tuple(offsets) == (0, 0, 2, 2, 3)
    with pytest.raises(ValueError, match=r"boundar|indic|index|offset|increasing"):
        ragged.RaggedDecoder("<f4", columns=1, allow_empty=False).unpack(
            source, index_count=5
        )
    empty = ragged.RaggedDecoder("<f4", columns=3, allow_empty=False).unpack(
        struct.pack("<q", 0), index_count=1
    )
    assert _rows(empty[0]) == ()
    assert tuple(empty[1]) == (0,)
    with pytest.raises(ValueError, match=r"boundar|indic|index|offset|increasing"):
        ragged.RaggedDecoder("<f4", columns=3, allow_empty=False).unpack(
            struct.pack("<2q", 0, 0), index_count=2
        )


@pytest.mark.usefixtures("backend")
def test_exact_bytes_results_retain_original_immutable_storage():
    np = require_backend("numpy")
    source = _wire([1.0, -2.0, 3.0, 4.0], [0, 1, 2])
    matrix, offsets = ragged.RaggedDecoder("<f4", columns=2).unpack(
        source, index_count=3
    )
    assert type(matrix) is type(offsets) is np.ndarray
    assert matrix.shape == (2, 2)
    assert offsets.shape == (3,)
    assert matrix.flags.c_contiguous
    assert offsets.flags.c_contiguous
    assert not matrix.flags.writeable
    assert not offsets.flags.writeable
    assert _owner(matrix) is source
    assert _owner(offsets) is source
    assert not matrix.flags.owndata
    assert not offsets.flags.owndata
    with pytest.raises(ValueError, match=r"read-only"):
        matrix[0, 0] = 7
    with pytest.raises(ValueError, match=r"read-only"):
        offsets[0] = 7
    del source
    assert matrix.tolist() == [[1.0, -2.0], [3.0, 4.0]]


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize(
    "exporter", ["bytearray", "readonly", "typed", "matrix", "scalar"]
)
def test_other_exporters_are_snapshotted_and_released(exporter):
    source = bytearray(_wire([1, 2, 3, 4], [0, 1, 2], value="=i", index="=I"))
    if exporter == "bytearray":
        buffer = source
    elif exporter == "readonly":
        buffer = memoryview(source).toreadonly()
    elif exporter == "typed":
        buffer = memoryview(source).cast("I")
    elif exporter == "matrix":
        buffer = memoryview(source).cast("I", (1, 7))
    else:
        source = bytearray(_wire([-7], [0, 1], value="=h", index="=B"))
        buffer = memoryview(source).cast("I", ())
    decoder = ragged.RaggedDecoder(
        "=i2" if exporter == "scalar" else "=i4",
        columns=1 if exporter == "scalar" else 2,
        index_dtype="u1" if exporter == "scalar" else "=u4",
    )
    matrix, offsets = decoder.unpack(
        buffer, index_count=2 if exporter == "scalar" else 3
    )
    if isinstance(buffer, memoryview):
        buffer.release()
    source[:] = bytes(len(source))
    source.extend(b"more")
    expected = ((-7,),) if exporter == "scalar" else ((1, 2), (3, 4))
    expected_offsets = (0, 1) if exporter == "scalar" else (0, 1, 2)
    assert _rows(matrix) == expected
    assert tuple(offsets) == expected_offsets


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("exporter", ["strided", "fortran"])
def test_non_c_contiguous_exporters_are_not_flattened(exporter):
    if exporter == "strided":
        source = memoryview(bytes(32))[::2]
    else:
        np = require_backend("numpy")
        source = np.zeros((3, 4), dtype="u1", order="F")
    with pytest.raises(BufferError):
        ragged.RaggedDecoder("u1", columns=1, index_dtype="u1").unpack(
            source, index_count=2
        )


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize(
    ("count", "exception"),
    [
        (0, ValueError),
        (-1, ValueError),
        (1.5, TypeError),
        ("2", TypeError),
        (3, ValueError),
        pytest.param(10**5000, ValueError, id="oversized-count"),
    ],
)
def test_index_count_is_integral_positive_and_fits_the_header(count, exception):
    with pytest.raises(exception):
        ragged.RaggedDecoder("<f4", columns=1).unpack(
            struct.pack("<2q", 0, 0), index_count=count
        )


@pytest.mark.parametrize(
    ("kwargs", "exception"),
    [
        ({"columns": 0}, ValueError),
        ({"columns": -1}, ValueError),
        ({"columns": 1.5}, TypeError),
        pytest.param({"columns": 10**5000}, ValueError, id="oversized-columns"),
        ({"columns": 1, "index_dtype": "f4"}, ValueError),
        ({"columns": 1, "finite": 1}, TypeError),
        ({"columns": 1, "allow_empty": 0}, TypeError),
        ({"columns": 1, "dtype": object()}, TypeError),
        ({"columns": 1, "dtype": "i04"}, struct.error),
    ],
)
def test_invalid_decoder_configuration_is_rejected(kwargs, exception):
    arguments = {"dtype": "<f4", **kwargs}
    with pytest.raises(exception):
        ragged.RaggedDecoder(**arguments)


@pytest.mark.usefixtures("backend")
def test_columns_and_count_accept_the_integer_index_protocol():
    class Indexed:
        def __init__(self, value):
            self.value = value

        def __index__(self):
            return self.value

    decoder = ragged.RaggedDecoder("<f4", columns=Indexed(2))
    assert decoder.columns == 2
    matrix, offsets = decoder.unpack(_wire([1.0, 2.0], [0, 1]), index_count=Indexed(2))
    assert _rows(matrix) == ((1.0, 2.0),)
    assert tuple(offsets) == (0, 1)


@pytest.mark.usefixtures("backend")
def test_repeated_mutable_input_is_revalidated_without_stale_results():
    source = bytearray(_wire([1.0, 2.0], [0, 2]))
    decoder = ragged.RaggedDecoder("<f4", columns=1, finite=True)
    first, _ = decoder.unpack(source, index_count=2)
    struct.pack_into("<I", source, len(source) - 4, 0x7F800001)
    with pytest.raises(ValueError, match=r"finite|NaN|inf"):
        decoder.unpack(source, index_count=2)
    assert _rows(first) == ((1.0,), (2.0,))


@pytest.mark.usefixtures("backend")
def test_numpy_free_results_are_owned_immutable_python_values(monkeypatch):
    monkeypatch.setattr(ragged.binpack, "_HAVE_NUMPY", False)
    monkeypatch.setattr(ragged.binpack, "np", None)
    matrix, offsets = ragged.RaggedDecoder(
        ">i4", columns=2, index_dtype=">u2", finite=True
    ).unpack(_wire([1, -2, 3, -4], [0, 1, 2], value=">i", index=">H"), index_count=3)
    assert type(matrix) is tuple
    assert all(type(row) is tuple for row in matrix)
    assert matrix == ((1, -2), (3, -4))
    assert offsets == (0, 1, 2)
    assert type(offsets) is tuple
    assert all(type(value) is int for row in matrix for value in row)
    assert all(type(value) is int for value in offsets)


def test_huge_columns_cannot_overflow_a_zero_row_shape():
    with pytest.raises(ValueError, match=r"column|size|large|overflow"):
        ragged.RaggedDecoder("f8", columns=sys.maxsize)


def test_public_annotations_support_module_class_and_method_introspection():
    assert get_type_hints(ragged)
    assert get_type_hints(ragged.RaggedDecoder)["columns"] is int
    assert get_type_hints(ragged.RaggedDecoder.unpack)["index_count"] is int
