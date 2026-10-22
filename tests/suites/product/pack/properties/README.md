# Binary packing properties

`test_pack_invariants.py` generates struct formats and values across byte orders and native alignment. Stdlib `struct` supplies the byte/value oracle; offset writes must preserve prefix/suffix bytes, and every generated truncated struct must raise `struct.error`.

These tests exercise the stdlib-compatible scalar surface and require no optional NumPy backend.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/pack/properties -q
```

See [the module test overview](../README.md) for related evidence.
