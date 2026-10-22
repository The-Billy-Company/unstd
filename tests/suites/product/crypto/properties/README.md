# BLAKE3 stream properties

`test_stream_laws.py` cuts published reference inputs at arbitrary and
block/chunk-edge offsets, including repeated cuts that make empty parts. It wraps
each part as bytes, bytearray, memoryview, read-only view or `array("B")`, and
feeds them through list, tuple and iterator constructors and chained updates.
Every segmentation must reproduce the BLAKE3 authors' vector. Arbitrary binary
parts must also equal the one-shot digest of their join.

A `RuleBasedStateMachine` interleaves updates, truncated reads and rebuilds from
recorded parts. Its message is always a prefix of the reference filler, so
published lengths are checked against the vector. Other lengths use the
documented join law, and repeated reads must not finalize the stream. Both the
Python update loop and the native batching helper are driven. Native cases skip
only when the optional helper was not built.

These tests require BLAKE3 from the `crypto` extra; `--require-extras` turns its
absence into a failure.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto/properties -q
```

See [the module test overview](../README.md) for related evidence.
