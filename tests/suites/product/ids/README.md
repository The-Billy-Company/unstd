# Identifier contracts

Contracts for `unstd.ids.uid`: UUIDv7 generation, stable UUIDv5 derivation, and fallback fork safety.

- [unit/](unit/README.md): generation, formatting, RFC 9562 published vectors, and fixed derivation vectors.
- [properties/](properties/README.md): generated UUIDv7 bit-layout and ordering laws, plus a fallback clock/entropy state machine.
- [integration/](integration/README.md): import-time backend selection and fork-context children of a lock-holding parent.
- [regression/](regression/README.md): inherited-lock deadlock after fork.

Generation works with the stdlib fallback or the optional `ids` backend. The fork cases require `os.fork`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/ids -q
```

See [the module documentation](../../../../src/unstd/ids/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
