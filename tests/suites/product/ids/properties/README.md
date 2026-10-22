# UUIDv7 layout properties

`test_uuid7.py` generates batches of UUIDs and decodes them with stdlib `uuid.UUID`. It checks RFC 9562 version/variant bits, the 48-bit monotone timestamp field, canonical and bare-hex forms, and detection of deliberately corrupted version/variant fields.

These laws exercise the active public generator and do not require the optional `ids` backend.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/ids/properties -q
```

See [the module test overview](../README.md) for related evidence.
