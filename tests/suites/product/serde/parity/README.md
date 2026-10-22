# Serialization differential contracts

These files compare JSON encoding/decoding and Base64 behavior with the running stdlib, including formatting, exact numbers/types, hooks, surrogate handling, malformed inputs, and error diagnostics. Fast-path tests observe backend use and the compatibility boundary rather than relying on a round trip.

The `test_jsonx_native*` and proof-boundary files also cover native ownership, recursion, callbacks, and extension reinitialization. Common parity runs on fallback paths; acceleration cases need their optional backends or compiled extension.

`test_jsonx_encode_parity.py` also freezes the strict identity byte format,
rejects coercions and invalid identity values, checks cycles/shared branches and
snapshot ownership, and retains the legacy identity conversion behavior.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/parity -q
```

See [the module test overview](../README.md) for related evidence.
