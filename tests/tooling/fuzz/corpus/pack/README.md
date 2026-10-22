# Numeric packing replay inputs

These version-1 envelopes drive the [pack target](../../targets/pack.py) and
`unstd.pack.binpack`. Byte 0 modulo three selects generated encoding, arbitrary
decoding, or named dtype aliases. Byte 1 selects one of eleven integer/float
kinds and four byte-order spellings; remaining bytes supply numeric samples or
encoded data. Missing header bytes select zero.

Cases cover signed/unsigned widths, half/single/double floats, aliases, byte
orders, NaNs/infinities/signed zero, empty data and partial trailing elements.
Independent `struct` bytes and values check sized/iterator encoding and
bytes/bytearray/memoryview decoding. Partial multibyte elements must fail.

`seeds.json` authenticates each complete envelope; see the
[corpus rules](../README.md) for validation and promotion. From the repository
root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target pack --backend stdlib
```

The installed backend requires `numpy`; both modes and campaign options are
explained in the [fuzz guide](../../README.md).
