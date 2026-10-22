# TOML replay inputs

These version-1 envelopes drive the [TOML target](../../targets/toml.py) and
`unstd.toml`. Byte 0 modulo two selects raw UTF-8 text (invalid octets become
surrogates) or a generated TOML-1.0 model; remaining bytes supply source or
model entropy. Empty input takes the generated-model path with empty entropy;
an explicit even selector with no payload selects an empty raw document.

Cases cover tables/arrays, ordering, Unicode/escapes, integer extrema,
nonfinite floats, dates/times, duplicate keys, malformed text and generated
models. Raw value/error and binary-handle ownership checks use `tomllib`.
Generated models have independent expected values; installed `tomlkit` also
checks writers and comment retention. Stdlib mode explicitly omits unavailable
writer paths while retaining read laws.

`seeds.json` authenticates each complete envelope under the
[shared corpus rules](../README.md). From the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target toml --backend stdlib
```

The installed backend requires `tomlkit`; preparation and campaign options are
in the [fuzz guide](../../README.md).
