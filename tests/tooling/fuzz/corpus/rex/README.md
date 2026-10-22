# Regex replay inputs

These version-1 envelopes drive the [regex target](../../targets/rex.py) and
`unstd.rex`. Byte 0 modulo two selects generated execution or raw-pattern
compilation. Byte 1 selects grammar, flags and text/bytes transport; remaining
bytes supply subjects and literals, or a raw pattern separated from a subject
by NUL. Missing selectors mean zero. Executed subjects combine fixed controls
with at most 4,096 payload bytes; arbitrary raw patterns are capped at 256 bytes
and only compiled (the following subject is unused in that mode).

Cases cover eligible native grammar, captures and anchors, Unicode/nullable/
lookaround/backreference fallback, escaped literals, compile errors and retained
surrogate-region regressions. Results, spans, substitutions, callback histories
and diagnostics compare with `re`. Dollar-anchor/POSIX divergences and hostile
nested repetition are compilation-only controls.

`seeds.json` authenticates each complete envelope under the
[corpus rules](../README.md). From the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target rex --backend stdlib
```

The installed backend requires `irgx`; see the [fuzz guide](../../README.md)
for setup, worker bounds and the limits of Python coverage evidence.
