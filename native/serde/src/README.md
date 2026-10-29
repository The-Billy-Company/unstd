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
For eligible shared-row lists, a separate writer frame retains up to 32
completed flat-container spans. Reuse requires object identity and equal depth;
every repeated logical value still consumes the visit budget. All span owners
are released before return, and each call emits its complete output. Ordinary
independent records do not allocate the span cache. Safe ASCII string spans
are copied in bulk rather than appended one character at a time.

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
`array.h` finds complete top-level array-item spans in bounded bytes or Unicode
windows. Bytes and one-byte strings skip payloads using the shared
quote/backslash scanner; wider Unicode retains code-point scanning.
`array_decode` wraps an owned span in one private bytes/Unicode allocation and
uses the existing decoder. It requires the original `loads` function and
unchanged decoder configuration. The streaming reader can prove an object
prefix directly through that decoder; other shapes use the native framer.
An incomplete short suffix can join one following window. Large items and
refused/invalid batches resume the portable walker with their original array
state, avoiding repeated prefix parses and retaining item diagnostics.
The result proof finishes each child before its sibling with at most 64 C
container frames. Its visit budget is the original document's byte/code-point
length: every newly parsed value consumes at least one source unit, so proof
work remains bounded by input size. Unlike arbitrary encoder inputs, this owned
tree cannot expand an alias-heavy DAG. Wide exact trees no longer hit a fixed
65,536-value limit and discard a complete parse. Numeric and depth refusals keep
their original precise-parser fallback.

`encode.h` and `decode.h` install vectorcall bindings only on CPython 3.14.
They retain the Python function objects and fall back to ordinary dispatch when
code, defaults, backend configuration or tracing/monitoring guards change.
`adapter.c` owns retained state, dictionary-watcher invalidation and teardown;
teardown detaches watchers and restores vectorcall slots. Review version gates
around private stack, dictionary and monitoring layouts before adding a minor
Python version. Keep refusal (`NotImplemented`) distinct from allocation errors.

CPython 3.15 keeps ordinary Python function dispatch and uses authenticated,
retained `sys.monitoring.get_events` and `get_local_events` C builtins to admit
batch decoding and identity snapshots. Each admission reads fresh event masks,
including local changes before the code object's instrumentation is refreshed.
Its unqualified private C-stack and dense-dictionary layouts remain disabled;
the precise parser and public dictionary traversal handle those cases.
