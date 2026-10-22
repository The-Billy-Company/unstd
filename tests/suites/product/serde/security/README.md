# Parser and stream security contracts

`test_parser_boundaries.py` covers malformed JSON and Base64, encoding/depth failures, line-delimited record boundaries, and observable read/write failures. Expectations come from stdlib codecs and explicit stream histories; NDJSON may discard malformed data only when its skip policy was requested.

Installed and forced-stdlib configurations are exercised. Hostile parser cases use bounded workers from test support; backend requirements follow the shared optional/required policy.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/security -q
```

See [the module test overview](../README.md) for related evidence.
