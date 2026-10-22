"""Array layouts from strided inputs, buffer mutability, and decoded-view lifetime.

Expected bytes come from stdlib ``struct`` over logical values listed by hand in
C (row-major) order; aliasing and lifetime are observed through the real buffers.
"""

from __future__ import annotations

from array import array
import gc
import struct

import pytest

from tests.support.environment.backends import require_backend
from unstd.pack import binpack


@pytest.fixture
def np():
    return require_backend("numpy")


@pytest.fixture
def fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
    monkeypatch.setattr(binpack, "np", None)


# 3x4 matrix 0..11; each view's logical C-order values written out by hand.
_VIEWS = {
    "transposed": (lambda m: m.T, [0, 4, 8, 1, 5, 9, 2, 6, 10, 3, 7, 11]),
    "column-step": (lambda m: m[:, ::2], [0, 2, 4, 6, 8, 10]),
    "reversed-rows": (lambda m: m[::-1], [8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3]),
    "reversed-flat": (lambda m: m.ravel()[::-3], [11, 8, 5, 2]),
    "fortran": (lambda m: m.copy(order="F"), list(range(12))),
}


@pytest.mark.parametrize("view", list(_VIEWS))
@pytest.mark.parametrize(
    ("source", "fmt"), [("<i4", "<i"), (">i4", ">i"), ("<i8", "<q"), ("<f8", "<d")]
)
def test_strided_and_foreign_order_ndarrays_pack_in_logical_c_order(
    np, view: str, source: str, fmt: str
):
    select, logical = _VIEWS[view]
    selected = select(np.arange(12).astype(source).reshape(3, 4))
    count = len(logical)
    assert binpack.pack_array(">i4", selected) == struct.pack(f">{count}i", *logical)
    assert binpack.pack_array(source, selected) == struct.pack(
        f"{fmt[0]}{count}{fmt[1]}", *logical
    )


def test_mutable_buffers_decode_to_writable_aliases(np):
    backing = bytearray(struct.pack("<3i", 1, 2, 3))
    view = binpack.unpack_array("<i4", backing)
    assert view.flags.writeable
    view[1] = -7
    assert struct.unpack("<3i", backing) == (1, -7, 3)
    backing[8:12] = struct.pack("<i", 99)
    assert view.tolist() == [1, -7, 99]


@pytest.mark.parametrize(
    "readonly",
    [
        lambda raw: raw,
        lambda raw: memoryview(bytearray(raw)).toreadonly(),
    ],
    ids=["bytes", "readonly-view"],
)
def test_readonly_buffers_decode_to_readonly_views(np, readonly):
    view = binpack.unpack_array("<i4", readonly(struct.pack("<2i", 5, 6)))
    assert not view.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        view[0] = 1
    assert view.tolist() == [5, 6]


def test_decoded_view_keeps_its_buffer_alive_and_exported(np):
    backing = bytearray(struct.pack("<2i", 41, 42))
    view = binpack.unpack_array("<i4", backing)
    with pytest.raises(BufferError):
        backing.extend(b"\x00" * 4)
    del backing
    gc.collect()
    assert view.tolist() == [41, 42]

    owner = bytearray(4)
    transient = binpack.unpack_array("<i4", owner)
    del transient
    gc.collect()
    owner.extend(bytes(4))
    assert len(owner) == 8


@pytest.mark.usefixtures("fallback")
def test_fallback_and_portable_decodes_release_the_buffer_immediately():
    backing = bytearray(struct.pack("<2i", 1, 2))
    decoded = [
        binpack.unpack_array("<i4", backing),
        binpack.unpack_array_portable("<i4", memoryview(backing)),
    ]
    backing.extend(struct.pack("<i", 3))
    backing[:4] = bytes(4)
    assert decoded == [[1, 2], [1, 2]]


def test_unaligned_offsets_decode_exact_values(np):
    raw = b"\xaa" + struct.pack("<2d", 1.5, -0.25) + b"\xbb"
    window = memoryview(raw)[1:-1]
    assert binpack.unpack_array("<f8", window).tolist() == [1.5, -0.25]
    assert binpack.unpack_array_portable("<f8", window) == [1.5, -0.25]


def test_noncontiguous_ndarrays_are_refused_not_flattened(np):
    strided = np.arange(12, dtype="<i4").reshape(3, 4)[:, ::2]
    with pytest.raises(ValueError, match="not C-contiguous"):
        binpack.unpack_array("<i4", strided)
    with pytest.raises(ValueError, match="not C-contiguous"):
        binpack.unpack_array_portable("<i4", strided)


@pytest.mark.parametrize(
    "exporter",
    [
        lambda: array("i", [1, -2]),
        lambda: memoryview(struct.pack("=2i", 1, -2)).cast("i"),
        lambda: bytearray(struct.pack("=2i", 1, -2)),
    ],
    ids=["array-i", "typed-view", "bytearray"],
)
@pytest.mark.usefixtures("fallback")
def test_fallback_decodes_any_contiguous_exporter_by_bytes(exporter):
    assert binpack.unpack_array("=i4", exporter()) == [1, -2]
    assert binpack.unpack_array("=u1", exporter()) == list(struct.pack("=2i", 1, -2))
