# Serialization API contracts

`test_b64.py` covers standard/URL Base64 and fallback parity. `test_jsonx.py` checks the explicit NumPy conversion extension. NDJSON tests cover LF-only record boundaries, lazy reads, line-numbered errors, write counts, schema validation, and stream/typing behavior. `test_structs.py` compares the typed Codec wrapper with msgspec directly. `test_structs_hooks.py` pins exact canonical/hex UUID and string/number Decimal bytes and decoded types, as well as the text seen by `float_hook` and NumPy coercion by `float_enc_hook`. It also covers enc/dec/float hook failures: their exception classes, messages and `$`-paths, `decode_or` propagation, and successful reuse of the same codec afterwards.

`test_jsonx_stream.py` checks bounded JSON-array reads, short reads and chunk boundaries, UTF-8/16/32 decoding, per-item hooks, malformed input and trailing-data errors, caller ownership, large records, and bounded whitespace buffering against independent stdlib expectations. Real gzip streams cover binary/text reads and corrupt trailers; isolated subprocesses prove the base path with optional backends and the native JSON extension absent.

`test_jsonx_dump_array.py` checks generator-based array exports against independent stdlib bytes, bounded requests and memory, oversized records, Unicode, callbacks, gzip, short and blocked writes, and caller ownership. An isolated subprocess proves operation without optional backends or the native extension.

Base JSON/Base64, JSON-array streaming and untyped NDJSON work without extras. NumPy, typed msgspec, accelerated codecs, and mypy cases require their respective installed capabilities; the shared collection policy omits structs when unavailable, and newer structs files gate on msgspec through the shared optional/required backend policy.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/unit -q
```

See [the module test overview](../README.md) for related evidence.
