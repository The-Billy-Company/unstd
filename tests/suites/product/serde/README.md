# Serialization contracts

Contracts for `unstd.serde`: JSON and Base64 compatibility, typed codecs, and streaming NDJSON.

- [unit/](unit/README.md): codec APIs, NumPy extension, typed records, and stream behavior.
- [parity/](parity/README.md): stdlib differential behavior and native dispatch boundaries.
- [properties/](properties/README.md): generated JSON and Base64 fidelity laws.
- [security/](security/README.md): malformed input, parser limits, and stream failures.
- [regression/](regression/README.md): reserved location for confirmed serialization defects.

The stdlib JSON/Base64 and untyped NDJSON contracts run without serde backends. Accelerated and typed cases need the corresponding `serde` dependencies; compiled-native cases require built extensions.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde -q
```

See [the module documentation](../../../../src/unstd/serde/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
