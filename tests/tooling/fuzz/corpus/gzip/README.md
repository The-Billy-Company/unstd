# Gzip replay inputs

These version-1 envelopes drive the [gzip target](../../targets/gzip.py).
Byte 0 modulo four selects a raw gzip stream, a generated member, generated
concatenated members, or generated CRC/truncation corruption. The remaining
bytes are the stream in raw mode or decoded source content otherwise; their
first byte also selects level, chunk size and corruption. Empty input uses raw
mode with an empty stream.

The corpus covers real and empty members, concatenation, inter-member padding,
CRC/ISIZE errors, bad framing, truncation and expansion-budget refusals, including
forged sizes. A bounded stdlib stream preflight enforces a 64-KiB decoded budget
before production decoding. Admitted cases compare stdlib results/errors across
one-shot, streaming, seek, readinto and compression paths.

`seeds.json` authenticates the complete envelopes under the
[shared corpus rules](../README.md). From the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target gzip --backend stdlib
```

The installed backend requires `zlib_ng`; see the [fuzz guide](../../README.md)
for dependencies, budgets and exact reproduction.
