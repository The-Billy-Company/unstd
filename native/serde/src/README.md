# JSON kernel source

[`_json_native.c`](_json_native.c) builds `unstd.serde._json_native`: exact-type
encoding proofs, backend bindings, and CPython runtime guards. Adjacent headers
hold [encoder dispatch](_json_encode_native.h),
[decoding](_json_decode_native.h), [NDJSON batching](_json_lines_native.h), and
[stdlib ASCII escaping](_json_text_native.h).

Encoding proves the live tree without allocation or Python callbacks, then
calls the retained `orjson` builtin in the same uninterrupted GIL span. Depth,
visit, and interpreter recursion budgets bound the proof, including wide and
indented paths that bypass Python snapshot size limits.

Decoding uses `msgspec` for exact numbers; larger documents may use `orjson`
with a bounded result proof and precise-parser retry. Rejected NDJSON rows
delegate the original whole input for Python line numbers and diagnostics.
CPython 3.14 `loads`/`dumpb` vectorcall bindings retain the Python function
objects and delegate to ordinary dispatch when code, defaults, backend configuration,
or tracing/monitoring guards fail. Private CPython layouts are version-gated;
review those gates when adding interpreter support.
