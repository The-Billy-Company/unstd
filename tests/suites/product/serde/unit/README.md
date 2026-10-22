# Serialization API contracts

`test_b64.py` covers standard/URL Base64 and fallback parity. `test_jsonx.py` checks the explicit NumPy conversion extension. NDJSON tests cover LF-only record boundaries, lazy reads, line-numbered errors, write counts, schema validation, and stream/typing behavior. `test_structs.py` compares the typed Codec wrapper with msgspec directly.

Base JSON/Base64 and untyped NDJSON work without extras. NumPy, typed msgspec, accelerated codecs, and mypy cases require their respective installed capabilities; the shared collection policy omits structs when unavailable.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/unit -q
```

See [the module test overview](../README.md) for related evidence.
