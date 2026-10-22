# NDJSON replay inputs

These version-1 inputs exercise `unstd.serde.ndjson` through the
[NDJSON target](../../targets/ndjson.py). Byte 0 modulo three selects physical
LF-delimited byte rows, UTF-8/surrogate-text rows, or generated records; the rest
is source or model entropy. Empty input selects empty byte rows. Generated mode
uses at most 16 rows; raw mode admits at most 512 physical lines and the JSON
target's lexical depth/digit bounds.

Cases cover blank rows, CRLF and final-newline choices, exotic whitespace,
Unicode separators, invalid UTF-8, malformed/truncated rows and writer round
trips. The independent per-line stdlib JSON model checks physical line numbers,
strict stopping, skip continuation, callback order and exception identity.

`seeds.json` authenticates the envelopes under the
[shared corpus rules](../README.md). From the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target ndjson --backend stdlib
```

The installed backend requires `orjson`; use the [fuzz guide](../../README.md)
for setup and campaign controls.
