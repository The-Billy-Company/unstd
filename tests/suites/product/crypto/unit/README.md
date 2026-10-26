# BLAKE3 known-answer vectors

`test_vectors.py` reads the vendored `blake3_reference_vectors.json` published by the BLAKE3 authors. Plain hashing, keyed MAC, and key derivation are checked against its independent expected outputs across chunk/tree boundaries, including streaming and output prefixes.

`test_hash_acceleration.py` compares large hashes, MACs, and derived keys with the serial backend around the 4 MiB worker-count boundary and at 8/16 MiB. It also checks buffer/error compatibility, public signatures, eager streaming, independent concurrent calls, bounded fork subprocesses with a prewarmed backend pool, and injected thread-creation failures with serial retry. Interrupts, validation errors, and unrelated backend panics must still propagate.

`test_hash_workers.py` covers the 1 MiB parallel crossover, adaptive worker counts,
CPU caps, and smaller-pool failure recovery. CPU caps retain Python 3.13's process
count precedence and use process affinity on Python 3.12, with a portable host
count fallback when affinity is missing or fails. `test_stream_batching.py`
exercises native and Python streaming against published vectors, reentrant
generators, mutable-buffer callbacks, live list mutation, invalid chunks, bounded
copies, and caller-object lifetimes.

`test_digest_contract.py` hashes every published input as bytes, bytearray,
memoryviews (read-only, `c`, 2-D) and `array` B/b, and checks `raw`, `hex`,
truncation, `derive_key` and `Stream`. It also covers exact rejection of strided,
wider-than-u8 and text buffers without changing stream state, and walks one
stream through all 35 published lengths with repeated reads. Further cases cover
truncation bounds, KDF context types, RFC 7693 BLAKE2b vectors for
`legacy_blake2b`, and bounded memory for a 16 MiB generator stream.

A package-specific envelope golden pins token composition separately. These tests require BLAKE3 from the `crypto` extra and are omitted from collection when the digest module is unavailable.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto/unit -q
```

See [the module test overview](../README.md) for related evidence.
