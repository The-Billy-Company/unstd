# Identifier contracts

Contracts for `unstd.ids.uid`: UUIDv7 generation, stable UUIDv5 derivation, and fallback fork safety.

- [unit/](unit/README.md): generation, formatting, and fixed derivation vectors.
- [properties/](properties/README.md): generated UUIDv7 bit-layout and ordering laws.
- [regression/](regression/README.md): inherited-lock deadlock after fork.

Generation works with the stdlib fallback or the optional `ids` backend. The fork regression requires `os.fork`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/ids -q
```

See [the module documentation](../../../../src/unstd/ids/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
