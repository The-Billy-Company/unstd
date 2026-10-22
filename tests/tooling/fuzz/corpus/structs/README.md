# Typed codec replay inputs

These version-1 inputs exercise `unstd.serde.structs` through the
[typed codec target](../../targets/structs.py). Byte 0 modulo four selects a
strict Row, coercing Row, tagged Put/Delete union, or list of up to eight Rows;
other selector bits choose encoder ordering. Remaining bytes are passed unchanged
to the wrapper and raw `msgspec` codec. Empty input selects an empty, invalid Row
document. Schemas and hooks are fixed in source.

Cases cover index/label/list bounds, coercion, tags, unknown fields, ordering,
UTF-8 failures, surrogate escapes, rejected UTF-16/32 and truncated JSON. The
oracle checks wrapper parity with raw `msgspec`, generated round trips and
`decode_or` exception/default rules. This tests a shared backend wrapper rather
than an independent JSON parser.

This target is installed-only and requires `msgspec`. After
[dependency preparation](../../README.md), run from the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target structs --backend installed
```

`seeds.json` authenticates the complete envelopes; follow the
[shared corpus rules](../README.md) before editing or promoting a case.
