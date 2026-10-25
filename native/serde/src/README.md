# JSON kernel source

[`adapter.c`](adapter.c) builds `unstd.serde._json_native` as one translation
unit. [`decode.h`](decode.h), [`lines.h`](lines.h), [`encode.h`](encode.h),
[`escape.h`](escape.h), [`repeat.h`](repeat.h) and [`identity.h`](identity.h)
contain static implementation fragments, not standalone
headers or compilation units; their include order follows shared declarations.

Encoding enters through `encode`, `encode_wide`, `encode_indent`, `encode_text`, `dumpb` or
`line` in `adapter.c`. A live exact-builtin proof and the retained `orjson`
builtin run in one uninterrupted GIL span. Do not allocate, invoke Python or
release the GIL during validation: container references are borrowed, and the
proof must still describe the tree serialized. Depth, visit and interpreter
recursion budgets bound the walk; wide/indented paths avoid snapshot size limits.
Spacing and CPython 3.12 indented-float normalization happen after serialization.
`escape.h` converts trusted UTF-8 bytes to ASCII JSON text, including DEL and
surrogate-pair escapes, without parsing the document again.

`repeat.h` validates and emits eligible float records into private bytes in one
GIL span. Its float and string spelling caches exist only for that operation;
the writer shares a 262,144-value budget with admission and caps private output
at 1 MiB. Only untracked buffers and raw formatter storage may be allocated
while input references are borrowed. Unsupported values discard the complete
private result and use the existing fallback.

`identity.h` copies strict JSON trees through owned builtin iterators. It pins
keys and values across allocation, keeps all partial outputs untracked and
private until validation completes, and checks Python walker metadata,
builtins, finiteness checks, observers and recursion headroom before and after
the copy. Shared acyclic branches receive independent copies. The Python
facade still invokes the original identity encoder, preserving its lookup
order and integer conversion limits. The retained
[`json_identity_gc` audit](../../../tests/tooling/runtime/json_identity_gc.py)
injects collection at allocation and ownership boundaries in an isolated
sanitizer build.

`decode.h` selects `msgspec` for small documents, low stack headroom and likely
float input. Larger documents may use `orjson`, but a bounded result walk must
reject every float before returning; otherwise the original document is retried
with `msgspec`. `lines.h` trims/skips blank rows and uses that same decoder;
refused or invalid rows discard the batch so Python can replay the whole input
with its line numbers and diagnostics. Allocation failures propagate.

`encode.h` and `decode.h` install vectorcall bindings only on CPython 3.14.
They retain the Python function objects and fall back to ordinary dispatch when
code, defaults, backend configuration or tracing/monitoring guards change.
`adapter.c` owns retained state, dictionary-watcher invalidation and teardown;
teardown detaches watchers and restores vectorcall slots. Review version gates
around private stack, dictionary and monitoring layouts before adding a minor
Python version. Keep refusal (`NotImplemented`) distinct from allocation errors.
