# Binary packing API contracts

`test_pack.py` compares struct-compatible operations byte for byte with stdlib `struct`, including endianness, native alignment, offsets, buffers, and exception identity. Array tests check formats, range/error behavior, and agreement between NumPy and the forced stdlib fallback.

The scalar and fallback paths run without extras. NumPy cases require its availability through the `pack` capability.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/pack/unit -q
```

See [the module test overview](../README.md) for related evidence.

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
