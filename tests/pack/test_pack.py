"""Adversarial + parity tests for ``unstd.pack.binpack``.

Two contracts are pinned here:

1. The struct-faithful surface is **byte-for-byte** identical to stdlib ``struct``
   across formats, endianness, and native padding/alignment — and re-exports the
   same ``error`` so existing ``except struct.error`` clauses keep catching.
2. ``pack_array``/``unpack_array`` round-trip, and — crucially — the NumPy backend
   and the stdlib-``struct`` fallback emit **identical bytes** and decode to
   **equal values**. The fallback path is forced deterministically (it is what
   runs on a base install); the NumPy path is exercised when NumPy is present.
"""

from __future__ import annotations

import struct

import pytest

from unstd.pack import binpack


try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # base install (this env) — the numpy-backend tests are skipped
    np = None
    _HAVE_NUMPY = False

requires_numpy = pytest.mark.skipif(
    not _HAVE_NUMPY, reason="numpy (`pack` extra) not installed"
)


# ── the struct-faithful surface: byte-identical to stdlib ``struct`` ───────────

# Cover signed/unsigned widths, floats, native alignment/padding, and every
# byte-order prefix (native/standard/little/big).
_FORMATS: list[tuple[str, tuple[object, ...]]] = [
    ("<Ihf", (1, -2, 3.5)),
    (">Ihf", (1, -2, 3.5)),
    ("=qQd", (-9, 9, 2.718281828)),
    ("!HBB", (65535, 7, 255)),
    ("<10s", (b"packbytes!",)),
    ("@ixd", (42, 3.0)),  # native alignment inserts a pad byte after the int
    ("<?bBhHiIqQefd", (True, -1, 2, -3, 4, -5, 6, -7, 8, 1.5, 2.5, 3.5)),
]


@pytest.mark.parametrize(("fmt", "vals"), _FORMATS)
def test_pack_is_byte_identical_to_stdlib(fmt: str, vals: tuple[object, ...]) -> None:
    """Test pack is byte identical to stdlib."""
    assert binpack.pack(fmt, *vals) == struct.pack(fmt, *vals)


@pytest.mark.parametrize(("fmt", "vals"), _FORMATS)
def test_unpack_matches_stdlib_and_round_trips(
    fmt: str, vals: tuple[object, ...]
) -> None:
    """Test unpack matches stdlib and round trips."""
    blob = struct.pack(fmt, *vals)
    assert binpack.unpack(fmt, blob) == struct.unpack(fmt, blob)
    assert binpack.unpack(fmt, binpack.pack(fmt, *vals)) == struct.unpack(fmt, blob)


def test_calcsize_matches_stdlib_including_alignment() -> None:
    """Test calcsize matches stdlib including alignment."""
    for fmt, _ in _FORMATS:
        assert binpack.calcsize(fmt) == struct.calcsize(fmt)


def test_pack_into_and_unpack_from_match_stdlib() -> None:
    """Test pack into and unpack from match stdlib."""
    buf = bytearray(binpack.calcsize("<Ihf"))
    ref = bytearray(struct.calcsize("<Ihf"))
    binpack.pack_into("<Ihf", buf, 0, 7, -3, 1.25)
    struct.pack_into("<Ihf", ref, 0, 7, -3, 1.25)
    assert buf == ref
    assert binpack.unpack_from("<Ihf", buf, 0) == struct.unpack_from("<Ihf", ref, 0)


def test_iter_unpack_matches_stdlib() -> None:
    """Test iter unpack matches stdlib."""
    blob = struct.pack("<4h", 1, 2, 3, 4)
    assert list(binpack.iter_unpack("<h", blob)) == list(struct.iter_unpack("<h", blob))


def test_struct_class_is_compiled_and_faithful() -> None:
    """Test struct class is compiled and faithful."""
    s = binpack.Struct("<Ihf")
    ref = struct.Struct("<Ihf")
    assert s.size == ref.size
    assert s.pack(9, -1, 2.5) == ref.pack(9, -1, 2.5)
    assert s.unpack(s.pack(9, -1, 2.5)) == ref.unpack(ref.pack(9, -1, 2.5))


def test_error_is_the_stdlib_error_and_is_raised() -> None:
    """Test error is the stdlib error and is raised."""
    assert binpack.error is struct.error
    with pytest.raises(binpack.error):
        binpack.pack("<I", -1)  # unsigned int cannot hold a negative
    with pytest.raises(struct.error):  # re-export catches under either name
        binpack.unpack("<I", b"\x00")  # too few bytes


# ── array helpers: the stdlib fallback path (what runs on a base install) ──────


@pytest.fixture
def force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the stdlib-``struct`` array path regardless of whether NumPy is present."""
    monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
    monkeypatch.setattr(binpack, "np", None)


_ARRAYS: list[tuple[str, list[object]]] = [
    ("<f4", [1.0, -2.5, 3.25, 0.0]),
    (">f8", [1.5, 2.5, -3.5]),
    ("<i2", [1, -2, 3, -32768, 32767]),
    (">u4", [0, 1, 4294967295]),
    ("<i8", [-9_000_000_000, 9_000_000_000]),
    ("u1", [0, 127, 255]),
    ("float32", [0.5, 1.5]),
]


@pytest.mark.usefixtures("force_fallback")
@pytest.mark.parametrize(("dtype", "seq"), _ARRAYS)
def test_fallback_pack_array_round_trips(dtype: str, seq: list[object]) -> None:
    """Test fallback pack array round trips."""
    blob = binpack.pack_array(dtype, seq)
    out = binpack.unpack_array(dtype, blob)
    assert isinstance(out, list)  # fallback returns a list, not an ndarray
    assert out == pytest.approx(seq)


@pytest.mark.usefixtures("force_fallback")
def test_fallback_pack_array_bytes_match_hand_written_struct() -> None:
    """Test fallback pack array bytes match hand written struct."""
    seq = [1.0, 2.0, 3.0, 4.0]
    assert binpack.pack_array("<f4", seq) == struct.pack("<4f", *seq)
    assert binpack.pack_array(">i2", [1, -2, 3]) == struct.pack(">3h", 1, -2, 3)


@pytest.mark.usefixtures("force_fallback")
def test_fallback_partial_trailing_element_raises() -> None:
    """Partial trailing bytes must raise ``struct.error``; valid blobs still unpack."""
    with pytest.raises(binpack.error):
        binpack.unpack_array("<f4", b"\x00\x00\x00")  # 3 bytes, itemsize 4
    # Fail-closed: a bad unpack must not poison subsequent valid traffic.
    assert binpack.unpack_array("<f4", struct.pack("<f", 1.5)) == pytest.approx([1.5])


@pytest.mark.usefixtures("force_fallback")
def test_fallback_unsupported_dtype_raises() -> None:
    """Unknown dtype codes must raise; supported dtypes remain usable afterward."""
    with pytest.raises(binpack.error):
        binpack.pack_array("<c16", [1])  # complex128 has no struct char
    assert binpack.pack_array("<f4", [1.0]) == struct.pack("<f", 1.0)


@pytest.mark.usefixtures("force_fallback")
def test_fallback_empty_sequence() -> None:
    """Test fallback empty sequence."""
    assert binpack.pack_array("<f4", []) == b""
    assert binpack.unpack_array("<f4", b"") == []


# ── array helpers: the NumPy backend + cross-backend byte parity ──────────────


@requires_numpy
@pytest.mark.parametrize(("dtype", "seq"), _ARRAYS)
def test_numpy_backend_bytes_equal_the_fallback_bytes(
    dtype: str, seq: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    # NumPy path (real module state).
    """Test numpy backend bytes equal the fallback bytes."""
    numpy_bytes = binpack.pack_array(dtype, seq)
    # Fallback path (forced), same inputs.
    monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
    monkeypatch.setattr(binpack, "np", None)
    fallback_bytes = binpack.pack_array(dtype, seq)
    assert numpy_bytes == fallback_bytes


@requires_numpy
@pytest.mark.parametrize(("dtype", "seq"), _ARRAYS)
def test_numpy_unpack_array_returns_zero_copy_view(
    dtype: str, seq: list[object]
) -> None:
    """Test numpy unpack array returns zero copy view."""
    blob = binpack.pack_array(dtype, seq)
    out = binpack.unpack_array(dtype, blob)
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.dtype(dtype)
    assert out.tolist() == pytest.approx(seq)
    # frombuffer over immutable bytes is a read-only, non-owning (zero-copy) view.
    assert (
        out.flags.writeable is False
    )  # numpy spells it "writeable"  # spellchecker:disable-line
    assert out.base is not None


@requires_numpy
def test_numpy_unpack_array_view_tracks_source_bytes() -> None:
    # A true view: mutating the backing buffer is reflected in the array.
    """Test numpy unpack array view tracks source bytes."""
    backing = bytearray(binpack.pack_array("<i4", [1, 2, 3]))
    view = binpack.unpack_array("<i4", memoryview(backing))
    backing[0:4] = struct.pack("<i", 999)
    assert view[0] == 999


# ── pack_array input-shape dispatch: sized inputs vs generic iterators ──────────


@requires_numpy
@pytest.mark.parametrize(("dtype", "seq"), _ARRAYS)
def test_numpy_pack_array_accepts_ndarray_directly(
    dtype: str, seq: list[object]
) -> None:
    """An ``ndarray`` input (the realistic "bulk array" call shape) must go
    straight through ``np.asarray`` — not be re-boxed into a Python list first —
    and still produce the exact same bytes as a plain-list input.
    """
    arr = np.asarray(seq, dtype=np.dtype(dtype))
    assert binpack.pack_array(dtype, arr) == binpack.pack_array(dtype, seq)


@requires_numpy
@pytest.mark.parametrize(("dtype", "seq"), _ARRAYS)
def test_numpy_pack_array_accepts_tuple_and_range_like_inputs(
    dtype: str, seq: list[object]
) -> None:
    """Test numpy pack array accepts tuple and other sized inputs."""
    assert binpack.pack_array(dtype, tuple(seq)) == binpack.pack_array(dtype, seq)


@requires_numpy
@pytest.mark.parametrize(("dtype", "seq"), _ARRAYS)
def test_numpy_pack_array_accepts_generic_generator(
    dtype: str, seq: list[object]
) -> None:
    """A sizeless iterator (a generator) has no ``__len__`` for ``np.asarray`` to
    use directly — it must route through ``np.fromiter`` instead of raising, and
    still agree byte-for-byte with the list-input path.
    """

    def _gen() -> object:
        yield from seq

    assert binpack.pack_array(dtype, _gen()) == binpack.pack_array(dtype, seq)


@requires_numpy
def test_numpy_pack_array_ndarray_input_is_not_reboxed_into_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the old ``np.asarray(list(seq), dtype)`` implementation forced
    every element through Python's ``list()`` even when ``seq`` was already an
    ``ndarray`` — ~395x slower on a 1M-element array. Pin that ``list()`` is no
    longer called on a sized input.
    """
    called = False
    real_list = list

    def _spy(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        return real_list(*args, **kwargs)

    monkeypatch.setattr("builtins.list", _spy)
    arr = np.arange(64, dtype=np.float32)
    binpack.pack_array("<f4", arr)
    assert called is False
