---
doc_radar:
  sentinels:
    - file: binpack.py
      contains: ['from unstd.pack.buffers import']
    - file: dtypes.py
      contains: ['def _struct_spec(']
    - file: ragged.py
      contains: ['class RaggedDecoder:', 'def unpack(']
---

# `pack` — binary packing

Part of [`unstd`](../README.md). A stdlib-`struct`-faithful surface plus
the bulk numeric-array pack/unpack `struct` handles clumsily, backed by NumPy when
the extra is installed and degrading to stdlib `struct` when it is not.

| Module                                                | Wraps                   | Use for                                                      |
| ----------------------------------------------------- | ----------------------- | ------------------------------------------------------------ |
| [`binpack`](#binpack--struct-faithful--array-packing) | stdlib `struct` · NumPy | format-string binary (de)serialization + bulk numeric arrays |
| [`ragged`](#ragged--validated-immutable-numeric-frames) | stdlib `struct` · optional native validator · NumPy | boundary tables followed by numeric row matrices |

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

# explicit portable validation and return types, with or without NumPy
blob = binpack.pack_array_portable("<i4", [1, 2, 3])  # -> bytes
values = binpack.unpack_array_portable("<i4", blob)  # -> owned list[int]
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
  `numpy.frombuffer` view over the buffer without a payload copy. Its writability
  follows the buffer: immutable bytes produce a read-only array, while a mutable
  buffer produces a writable view. Copy it (`arr.copy()`) for independent data.
- **Without the extra (base install):** both fall back to stdlib `struct` — the
  portable dtype subset, with `unpack_array` returning a `list` of Python scalars
  instead of an `ndarray`. Compatible values produce the same binary layout.

Input conversion and errors follow the selected backend. For example, NumPy
converts `1.5` to `1` for `"<i4"`, while `struct` rejects a float for that integer
format. Existing helpers retain these conversion and return-type differences
for compatibility. Choose the explicit portable helpers below when validation
and output types must be independent of installed dependencies.

With the default `count=-1`, both paths require the remaining buffer's byte size
to be a whole multiple of the dtype's itemsize; a
partial trailing element raises `binpack.error` (fallback) / `ValueError`
(NumPy `frombuffer`). Contiguous typed and multidimensional memoryviews are
interpreted as raw bytes, using all their storage regardless of their shape.

### `pack_array_portable` / `unpack_array_portable` — stable struct semantics

These helpers preserve stdlib `struct` conversion rules, even when NumPy is
installed. They
accept the portable numeric dtype subset and aliases above, apply the same
byte-order rules, and produce `bytes` / a fresh `list` of Python scalars.
Integer dtypes require integer values (or `__index__`), reject floats instead of
truncating them, and reject out-of-range integers with `binpack.error`.
Float conversion and overflow follow `struct.pack`; for example, finite values
outside the float16 range raise `OverflowError`. Nonfinite floats and signed
zero retain struct's representation.

```python
binpack.pack_array_portable("u1", [1.9])  # raises binpack.error on every install
binpack.pack_array_portable("u1", [256])  # raises binpack.error (out of range)
binpack.pack_array_portable("<i2", (n for n in [1, -2]))  # b"\x01\x00\xfe\xff"
binpack.unpack_array_portable("<i2", b"\x01\x00\xfe\xff")  # [1, -2]
```

Packing snapshots the iterable once before numeric conversion. Unpacking
requires contiguous buffers and, with the default `count=-1`, a whole number of
remaining elements; trailing partial elements raise `binpack.error`. Typed,
scalar, and multidimensional
memoryviews, `array.array` and ndarrays use their full byte storage. Dtype
strings must be an exact typestr or alias: `"i 4"`, `"i04"` or other numeric
spellings of a size raise `binpack.error`. Decoded lists do not alias the input
buffer. This stable contract gives up NumPy's broader dtype/coercion surface and
its zero-copy array result.

Packing exact one-dimensional `array.array` and NumPy `ndarray` inputs can
snapshot their primitive storage directly. This shortcut requires matching
integer signedness and width, or float64 input and output; it also handles byte
order and one-dimensional ndarray strides. Float16/float32, conversions between
numeric types, subclasses, and other iterables retain the tuple-and-`struct`
path, including its NaN conversion and custom iterator behavior. The shortcut
does not require NumPy for stdlib arrays and always returns owned bytes.

The [backend evaluation](../../../bench/reports/pack/README.md) compares a frozen
implementation with these changes on macOS and Linux. It distinguishes typed
array gains from representative compiled-`Struct` and owned-list workflows.

### Decode regions of a buffer

Both decoders accept keyword-only `offset` in **bytes** and `count` in
**elements**. The defaults decode the whole buffer. A positive offset skips a
header without slicing; an explicit nonnegative count selects a prefix and
allows unrelated trailing bytes. Unaligned offsets work too.

```python
import struct

framed = b"HDR" + struct.pack("<3f", 1.0, 2.0, 3.0) + b"tail"
view = binpack.unpack_array("<f4", framed, count=3, offset=3)
values = binpack.unpack_array_portable("<f4", framed, count=3, offset=3)
# view: ndarray sharing framed when NumPy is installed; list otherwise
# values: an independent [1.0, 2.0, 3.0] on every installation
```

With `count=-1`, decoding consumes the whole remainder and rejects partial
elements. The portable decoder accepts `__index__` counts and offsets, rejects
counts below `-1` with `ValueError`, and raises `binpack.error` for byte ranges
outside the buffer. The NumPy decoder retains `numpy.frombuffer` dtype,
conversion, view ownership, and error behavior. Callers requiring an ndarray
for reshaping can use `numpy.asarray(view)`; this retains an existing ndarray
without copying its data.

The [consumer API qualification](../../../bench/reports/pack/consumer-api/README.md)
maps the new buffer-selection options to live consumers and measures their
complete decode/publication operation.

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
The portable implementation also includes the primitive-buffer shortcut above.
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

## `ragged` — validated immutable numeric frames

`RaggedDecoder` compiles a layout containing an integer boundary table followed
by contiguous numeric rows. It owns geometry, boundary validation, finite-value
checks, and immutable storage. The caller supplies the table length and retains
protocol identity, publication budgets, and normalization policy.

```python
import struct
from unstd.pack import ragged

decoder = ragged.RaggedDecoder(
    "<f4", columns=2, index_dtype="<i4", finite=True, allow_empty=False
)
wire = struct.pack("<3i6f", 0, 1, 3, 1.5, -2.0, 3.0, 4.0, 5.5, 6.0)
rows, boundaries = decoder.unpack(wire, index_count=3)
# rows: [[1.5, -2.0], [3.0, 4.0], [5.5, 6.0]]
# boundaries: [0, 1, 3], describing segments of one row and two rows
```

Dtypes use the portable numeric grammar above; boundary dtypes must be
integers. `columns` must be positive and `index_count` at least one; both accept
`__index__`. Every remaining byte must belong to a complete matrix row.
Boundaries start at zero, end at the row count, and cannot decrease. The default
`allow_empty=True` permits equal adjacent boundaries; `False` requires strict
increases. The default `finite=False` preserves nonfinite floating values;
`finite=True` rejects NaNs and infinities anywhere in the frame, including
signaling NaNs and nonfinite values in the last row.

With NumPy installed, results are a read-only two-dimensional array and a
read-only one-dimensional boundary array. Exact `bytes` back them without a
payload copy. Every other C-contiguous exporter is copied **before** validation,
including a read-only view over mutable storage. Strided exporters raise
`BufferError`. Both arrays keep the whole frame alive; copy a selected array if
it must outlive the rest without retaining the frame. Without NumPy, results
are immutable tuples of rows and integer boundaries.

The optional [native validator](../../../native/pack/README.md) fuses checked
geometry and integer-boundary admission with IEEE exponent-bit scans. It needs
no NumPy C ABI, checks cancellation during large scans, and keeps no payload or
validation cache. A stdlib implementation enforces the same contract when the
extension is unavailable. Invalid geometry, boundaries, or enabled finite
constraints raise `ValueError`; malformed dtype spellings retain `struct.error`.

Callers should consume the validated arrays directly. Repeating finite scans
and snapshots afterward would repeat work already done here. Retain
application-specific admission and result construction. The
[complete-decoder qualification](../../../bench/reports/pack/ragged/README.md)
records those costs and the requested 100× target separately.

Kernel version 2 uses an exact NEON magnitude-bit maximum for float32 chunks
of at least 64 KiB and retains the smaller-frame classifier. The
[continued qualification](../../../bench/reports/pack/ragged/alternatives/native2.md)
includes installed Linux/macOS wheels, current project assembly, maximum recipe
capacity, and rejected backend alternatives. The complete 100× target remains
unmet.

## Source ownership

The package is one binary packing code leaf. [`binpack.py`](binpack.py) owns
public calls, direct stdlib aliases and optional NumPy dispatch. Its portable
format and buffer routines live beside it as focused modules.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Package entry point and usage contract |
| [`binpack.py`](binpack.py) | Public scalar and homogeneous-array operations |
| [`dtypes.py`](dtypes.py) | Numeric dtype grammar, aliases, byte order and bounded struct caches |
| [`buffers.py`](buffers.py) | Primitive-buffer snapshots preserving struct conversion semantics |
| [`ragged.py`](ragged.py) | Numeric-frame layouts, immutable ownership, validation, and optional native dispatch |

`dtypes.py` uses only stdlib `struct`. `buffers.py` avoids scalar boxing for
exact one-dimensional primitive arrays with matching integer signedness/width
or float64. Other inputs retain the iterable/struct path; NumPy is inspected
only if the caller already loaded it.

Run the contracts from the repository root with
`uv run --no-sync python -m pytest tests/suites/product/pack -q`.
