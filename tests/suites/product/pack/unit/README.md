# Binary packing API contracts

`test_pack.py` compares struct-compatible operations byte for byte with stdlib `struct`, including endianness, native alignment, offsets, buffers, and exception identity. Array tests check formats, range/error behavior, and agreement between NumPy and the forced stdlib fallback.

The scalar and fallback paths run without extras. NumPy cases require its availability through the `pack` capability.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/pack/unit -q
```

See [the module test overview](../README.md) for related evidence.

`test_portable_arrays.py` pins explicit portable helpers to struct conversion, overflow, wire layout, buffer ownership, dtype validation, and numeric protocols with and without NumPy. Legacy NumPy coercion and zero-copy results remain covered.
