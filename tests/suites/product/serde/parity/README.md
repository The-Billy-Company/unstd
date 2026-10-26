# Serialization differential contracts

These files compare JSON encoding/decoding and Base64 behavior with the running stdlib, including formatting, exact numbers/types, hooks, surrogate handling, malformed inputs, and error diagnostics. Fast-path tests observe backend use and the compatibility boundary rather than relying on a round trip.

The `test_jsonx_native*` and proof-boundary files also cover native ownership, recursion, callbacks, and extension reinitialization. Common parity runs on fallback paths; acceleration cases need their optional backends or compiled extension.

`test_jsonx_encode_guarantees.py` retains wide-proof work limits, shared-graph
rejection, mutation/callback behavior, partial writes and small-stack checks.
`test_jsonx_native_memory.py` retains native buffer stress and allocation soaks;
run its `resources` lane explicitly to check retention. The
[JSONX safety report](../../../../../bench/reports/serde/jsonx-safety.md) records
optimized/debug-allocator and ASan/UBSan verification.

`test_jsonx_encode_parity.py` also freezes the strict identity byte format,
rejects coercions and invalid identity values, checks cycles/shared branches and
snapshot ownership, and retains the legacy identity conversion behavior.

`test_jsonx_identity_native.py` checks owned native copies, partial-output
privacy, live-source mutation, observer/metadata guards and reentrant errors.
`test_jsonx_identity_dispatch.py` freezes encoder lookup order and Python
fallback visibility. `test_jsonx_repeated_float_native.py` checks operation-local
spelling caches, exact float bits and escapes, late declines, work limits and
mutation around the uninterrupted native writer.

The retained [`json_identity_gc`](../../../../tooling/runtime/json_identity_gc.py)
runner builds an isolated ASan/UBSan extension with collection injected at
allocation and ownership boundaries and sweeps deterministic allocation and
insertion failures. It checks private-output visibility and reference cleanup
at the failed boundary, including failure during metadata capture. Its source
checks require the injection
sites to be reviewed when the implementation changes; production sources are
never instrumented in place.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/serde/parity -q
```

See [the module test overview](../README.md) for related evidence.
