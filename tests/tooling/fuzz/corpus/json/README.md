# JSON replay inputs

These version-1 inputs drive the [JSON target](../../targets/json.py) against
`unstd.serde.jsonx`. Byte 0 modulo four selects raw bytes decode, UTF-8/surrogate
text decode, byte-derived value encoding, or parse-then-encode; remaining bytes
supply the payload. Empty input selects empty raw bytes.

Retained cases cover Unicode and UTF-16/32 transport, malformed UTF-8, surrogate
handling, duplicate-key order, signed zero, nonfinite values, truncation and
encode options. Separate boundary seeds exercise the harness's depth-96 and
512-digit-run admission limits and inputs outside that domain. Those limits
bound campaigns; they do not define the public JSON API. Comparisons use stdlib
JSON, preserving exact builtin types, order and error diagnostics.

`seeds.json` authenticates every complete envelope; follow the
[corpus rules](../README.md) when changing inputs. From the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target json --backend stdlib
```

The installed backend requires `orjson`; see the [fuzz guide](../../README.md)
for preparation and mutation campaigns.
