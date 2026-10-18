# Faithful stdlib JSON behavior

`serde.jsonx` now guarantees stdlib defaults and semantics while retaining
native acceleration only after bounded eligibility proofs. This restores exact
formatting, arbitrary precision integers, non-finite floats, Unicode/surrogates, UTF-16/32 binary
decoding, supported subclasses, key coercion/sorting, callbacks, custom codec
keywords, error diagnostics, interpreter limits and streamed writes.
Type annotations also accept duck-typed writers and typed `default` callbacks.

This intentionally changes the previous compact/unescaped `dumps` defaults and
removes silent data loss and orjson-only type acceptance. Request compact text
with `ensure_ascii=False, separators=(",", ":")`. Encoding snapshots at most
16 exact builtin values before calling orjson, retaining exact whitespace and
escaping; other inputs use cached or direct stdlib encoders. Decoding verifies
the complete backend result and rejects every float, including lossy large
integer conversions, before exposing it. Hooks/custom codecs bypass speculation.
Deep/unproven cases and backend errors use the original input with stdlib.
`dump` retains stdlib streaming; `load` reads once and shares the guarded parser.
`dumpb` now also preserves exact compact stdlib float notation across backends.

Benchmarks separately measure fallback records and eligible text/integer cases.
Python 3.13.14 measured 5.98× text encoding and 1.91× integer-array decoding.
The old speed floors covered incompatible behavior and were reset; no uniform
speedup is promised. Generated tests assert real backend use and cover callback
isolation, snapshot mutation, DEL escaping, and near-limit recursion failures.

The `numpy=True` conversion extension is consistent across backends. With an
explicit custom `cls`, `dump`/`dumps` forward all constructor keywords unchanged.
NDJSON and base64 JSON helpers preserve the same numeric values, and NDJSON
wraps Unicode, integer-limit and recursion failures with line numbers and honors
`skip_errors=True`.

Differential tests use the running stdlib as the authority, covering Python
3.12–3.14 and optional-backend/base configurations. Property tests now include
large integers, non-finite values, lone surrogates, mixed keys, formatting options,
binary encodings and malformed input instead of excluding backend differences.
