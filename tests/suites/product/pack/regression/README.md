# Binary packing regressions

`test_dtype_spelling.py` retains the loose dtype parser. Item sizes were read
with `int()`, so the portable helpers and the stdlib fallback accepted `"i4 "`,
`"i0_4"`, `"i+4"` and non-ASCII digits as `int32`. Only exact typestrs and
documented aliases are now accepted.

`test_buffer_byte_size.py` retains the struct-path buffer sizing defect. Only
memoryviews were measured in bytes. An 8-byte `array("i")` was rejected as
"buffer size 2", and contiguous ndarrays were misreported or failed in `struct`.
Every exporter is now measured by its byte size, with values derived by hand
through stdlib `struct`.

Both files failed against the original implementation. The ndarray case requires
NumPy from the `pack` extra; the rest need no extras.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/pack/regression -q
```

See [the module test overview](../README.md) for related evidence.
