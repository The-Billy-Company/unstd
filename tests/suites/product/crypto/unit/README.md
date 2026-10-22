# BLAKE3 known-answer vectors

`test_vectors.py` reads the vendored `blake3_reference_vectors.json` published by the BLAKE3 authors. Plain hashing, keyed MAC, and key derivation are checked against its independent expected outputs across chunk/tree boundaries, including streaming and output prefixes.

`test_hash_acceleration.py` compares large hashes, MACs, and derived keys with the serial backend around the 4 MiB worker-count boundary and at 8/16 MiB. It also checks buffer/error compatibility, public signatures, eager streaming, independent concurrent calls, bounded fork subprocesses with a prewarmed backend pool, and injected thread-creation failures with serial retry. Interrupts, validation errors, and unrelated backend panics must still propagate.

`test_hash_workers.py` covers the 1 MiB parallel crossover, adaptive worker counts, CPU caps, and smaller-pool failure recovery. `test_stream_batching.py` exercises native and Python streaming against published vectors, reentrant generators, mutable-buffer callbacks, live list mutation, invalid chunks, bounded copies, and caller-object lifetimes.

A package-specific envelope golden pins token composition separately. These tests require BLAKE3 from the `crypto` extra and are omitted from collection when the digest module is unavailable.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto/unit -q
```

See [the module test overview](../README.md) for related evidence.
