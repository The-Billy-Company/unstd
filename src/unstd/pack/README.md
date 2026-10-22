# `pack` — binary packing

Part of [`unstd`](../README.md). A stdlib-`struct`-faithful surface plus
the bulk numeric-array pack/unpack `struct` handles clumsily, backed by NumPy when
the extra is installed and degrading to stdlib `struct` when it is not.

| Module                                                | Wraps                   | Use for                                                      |
| ----------------------------------------------------- | ----------------------- | ------------------------------------------------------------ |
| [`binpack`](#binpack--struct-faithful--array-packing) | stdlib `struct` · NumPy | format-string binary (de)serialization + bulk numeric arrays |

## `binpack` — `struct`-faithful + array packing

stdlib `struct` is already a C codec — no third-party backend beats it on the
**scalar** path — so `binpack` re-exports that surface unchanged (byte-for-byte
identical) and adds fast, ergonomic **array** helpers on top.

```python
from unstd.pack import binpack

# stdlib-struct-faithful surface (identical bytes to `struct`)
raw = binpack.pack("<Ihf", 1, 2, 3.0)
a, b, c = binpack.unpack("<Ihf", raw)
n = binpack.calcsize("<Ihf")
s = binpack.Struct("<Ihf")  # compiled-format class
try:
    binpack.unpack("<I", b"\x00")  # -> raises binpack.error (== struct.error)
except binpack.error:
    ...

# bulk homogeneous arrays (the value-add)
blob = binpack.pack_array("<f4", [1.0, 2.0, 3.0])  # -> bytes
arr = binpack.unpack_array("<f4", blob)  # ndarray (numpy) | list (fallback)
```

### Migrating a file

1. Replace `import struct` with `from unstd.pack import binpack`.
2. Rename call sites: `struct.pack` → `binpack.pack`, `struct.unpack` →
   `binpack.unpack`, `struct.error` → `binpack.error`, `struct.Struct` →
   `binpack.Struct`, etc. The full surface (`pack`, `unpack`, `unpack_from`,
   `pack_into`, `calcsize`, `iter_unpack`, `Struct`, `error`) is re-exported.
3. Replace the awkward `struct.pack(f"<{len(xs)}f", *xs)` /
   `struct.iter_unpack` array idioms with `binpack.pack_array` /
   `binpack.unpack_array`.

### `pack_array` / `unpack_array` — the accelerated array seam

The portable dtype subset is signed/unsigned integers of 1, 2, 4, or 8 bytes
and floats of 2, 4, or 8 bytes. Use typestrs (`"<f4"`, `">i8"`, `"=u2"`, `"u1"`)
or their spelled-out names (`"float32"`, `"uint16"`, etc.). Endianness prefixes
`<` / `>` are explicit; `=` / `|` / no prefix select native byte order on the
fallback. NumPy accepts additional dtype forms, including booleans and complex
numbers, which the fallback does not implement.

- **With the `pack` extra (NumPy present):** `pack_array` encodes via
  `numpy.asarray(seq, dtype).tobytes()`; `unpack_array` returns a **zero-copy**
  `numpy.frombuffer` view over the buffer (no allocation, no copy). Its writability
  follows the buffer: immutable bytes produce a read-only array, while a mutable
  buffer produces a writable view. Copy it (`arr.copy()`) for independent data.
- **Without the extra (base install):** both fall back to stdlib `struct` — the
  portable dtype subset, with `unpack_array` returning a `list` of Python scalars
  instead of an `ndarray`. Compatible values produce the same binary layout.

Input conversion and errors follow the selected backend. For example, NumPy
converts `1.5` to `1` for `"<i4"`, while `struct` rejects a float for that integer
format. Callers needing backend-independent behavior should provide values
already valid for the portable dtype rather than relying on NumPy coercion.

Both paths require the buffer's byte size to be a whole multiple of the dtype's itemsize; a
partial trailing element raises `binpack.error` (fallback) / `ValueError`
(NumPy `frombuffer`). Contiguous typed and multidimensional memoryviews are
interpreted as raw bytes, using all their storage regardless of their shape.

#### Hold your data in an array, not a list

The speedup lives in the shape you hand it, and one of these rows is a loss.
Regenerate with `python3 -m bench --group pack --markdown`:

| Case | `binpack` | stdlib `struct` | Speedup |
| --- | --- | --- | --- |
| `pack_array("<f8", list)` — 50k | 631.4 µs | 364.7 µs | 0.6× |
| `pack_array("<f8", ndarray)` — 50k | 5.2 µs | 819.6 µs | **159.1×** |
| `unpack_array("<f8", buf)` — 50k | 281 ns | 470.7 µs | **1678.0×** |

Handed a Python list, `pack_array` is **slower** than `struct.pack`, and this is
structural rather than a defect: `struct.pack` with a repeat-count format unboxes
50 000 Python floats in a tight C loop writing straight into the output buffer,
while NumPy must materialize an ndarray first. Nothing wins that job —
`array.array` and `numpy.fromiter` were both measured and both lose to `struct`
as well.

Handed an ndarray it already owns, the encode is a memcpy; and `unpack_array`
never copies at all. That is the seam: it is for data that is *already* an array,
or about to become one. If you have a list and want bytes once, reach for
`binpack.pack` and the `struct` surface beside it.

### Backend & fallback

The scalar `struct` surface is pure stdlib and needs no extra. The array helpers
prefer NumPy (the `pack` extra, `numpy>=2.5`) and otherwise use `struct`.
Tests compare the portable numeric layouts and buffer behavior in
[`tests/suites/product/pack/unit/test_pack.py`](../../../tests/suites/product/pack/unit/test_pack.py).

### Prior art

- stdlib [`struct`](https://docs.python.org/3/library/struct.html) — the
  format-string binary codec this surface stays faithful to.
- NumPy [`frombuffer`](https://numpy.org/doc/stable/reference/generated/numpy.frombuffer.html)
  / `ndarray.tobytes` (Harris et al., _Array programming with NumPy_, Nature 2020)
  — the zero-copy buffer↔array bridge the accelerated array path rides.
- Structure-aware numeric codecs (byte-plane splitting for f32, int8 scalar
  quantization, a self-describing frame header) — the direction a future `pack`
  array backend would go; NumPy is the backend for now.
