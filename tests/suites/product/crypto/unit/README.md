# BLAKE3 known-answer vectors

`test_vectors.py` reads the vendored `blake3_reference_vectors.json` published by the BLAKE3 authors. Plain hashing, keyed MAC, and key derivation are checked against its independent expected outputs across chunk/tree boundaries, including streaming and output prefixes.

A package-specific envelope golden pins token composition separately. These tests require BLAKE3 from the `crypto` extra and are omitted from collection when the digest module is unavailable.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto/unit -q
```

See [the module test overview](../README.md) for related evidence.
