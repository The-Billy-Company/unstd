# Serialization regressions

Minimized confirmed defects in `unstd.serde`, each with an independent
assertion and a reference to the original failure.

`test_decode_or_unicode.py`: `structs.Codec.decode_or` leaked the
`UnicodeError` msgspec raises for non-UTF-8 JSON string content (or a `str`
payload with lone surrogates) instead of returning the default. RFC 8259
requires UTF-8, so these payloads are malformed cache misses. A hook's own
non-validation exception must still propagate.

These cases require msgspec from the `serde` extra; under `--require-extras` a
missing backend fails instead of skipping.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/regression -q
```

See [the module test overview](../README.md) for related evidence.
