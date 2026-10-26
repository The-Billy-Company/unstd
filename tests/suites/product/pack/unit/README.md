# Binary packing API contracts

`test_pack.py` compares struct-compatible operations byte for byte with stdlib `struct`, including endianness, native alignment, offsets, buffers, and exception identity. Array tests check formats, range/error behavior, and agreement between NumPy and the forced stdlib fallback.

The scalar and fallback paths run without extras. NumPy cases require its availability through the `pack` capability.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/pack/unit -q
```

See [the module test overview](../README.md) for related evidence.

`test_array_ranges.py` checks byte offsets and element counts against struct,
including unaligned windows, unrelated trailing bytes, typed exporters,
ownership, bounds, and byte-buffer subclasses that refuse slicing.
Default-valued range objects retain NumPy's index hooks and validation order;
float and bool range arguments keep the selected backend's behavior.

`test_ragged.py` checks numeric-frame geometry, monotonic boundaries, optional
finite constraints, immutable snapshots and views, tuple fallback, and runtime
annotations. `test_native_ragged.py` challenges the private kernel's types,
integer bounds, formats, unaligned fields, late nonfinite words, and recovery
after failures. Both native and Python validation paths must reject corrupt
frames without retaining mutable exports.

`test_dtype_matrix.py` checks every typestr, byte-order prefix and spelled-out
alias against hand-written little-endian bytes for min/max, subnormals, signed
zero and infinities, on both backends. It also covers one-past-limit integer
rejection, IEEE round-half-to-even float16/float32 ties, and overflow ties
(struct refuses, NumPy returns infinity). NaN payloads are compared with
`struct` and with NumPy's bit-exact views. Further cases cover partial trailing
bytes, malformed dtypes, and identity of the re-exported `struct` objects.

`test_array_views.py` packs transposed, stepped, reversed, Fortran-order and
foreign-endian ndarrays in logical C order. It checks writable and read-only
decoded views, export locks that keep the buffer alive, unaligned windows, and
refusal of non-contiguous ndarrays. The struct fallback must release the buffer
immediately and measure any contiguous exporter by bytes.

`test_portable_arrays.py` pins explicit portable helpers to struct conversion, overflow, wire layout, buffer ownership, dtype validation, and numeric protocols with and without NumPy. Legacy NumPy coercion and zero-copy results remain covered.

`test_buffer_packing.py` compares primitive array snapshots with struct across
integer widths, byte orders, reversed and stepped ndarrays, and floating-point
NaN payloads. It retains subclass iteration, dimensionality failures, range
checks, conversions between types, and release of buffer exports.
