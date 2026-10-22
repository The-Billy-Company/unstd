# Cryptography regressions

`test_derive_key_context.py` retains the KDF mode drop: `digest.derive_key(None, material)`
returned `digest.raw(material)` because the BLAKE3 backend reads a `None`
context as plain hashing. A missing context must raise `TypeError` on the serial
and parallel paths, and the published `derive_key` vector must still hold
afterwards. The test failed against the original implementation.

These tests require BLAKE3 from the `crypto` extra; `--require-extras` turns its
absence into a failure.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto/regression -q
```

See [the module test overview](../README.md) for related evidence.
