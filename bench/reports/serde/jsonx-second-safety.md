# Second-pass JSONX safety qualification

The finite-float writer and owned strict snapshots retain the existing fallback,
formatting, observer and interpreter-limit guarantees. Qualification uses the
corrected source freeze `914a990687481d7e415d1c2d61f1445591fb2f4db39fad58b7d174ad93471a85`,
recorded in the [source manifest](jsonx-second-source-manifest.json).

## Final checks

| CPython ABI | Default sanitizer suite: passed | Explicit resource checks: passed | Injected GC and allocation-failure checks: passed |
| --- | ---: | ---: | ---: |
| 3.12 | 3,128 | 5 | 35 |
| 3.13 | 3,117 | 4 | 35 |
| 3.14 | 3,139 | 4 | 35 |

The 3.13/3.14 resource lanes skip the 3.12-only float-normalization case. All
recorded final lanes exited successfully with no ASan or UBSan diagnostics:
**9,502 passed, 170 skipped, zero failures** across these runs.
Stack-use-after-return detection was enabled. The
[raw evidence](jsonx-second-native-safety.json) retains the
exact commands, source and binary hashes, runtime options, test counts and
allocation samples.

The unchanged **4 KiB** per-operation retention bound passes on all three ABIs.
After warmup, each soak measures 300 calls in twelve batches with collection
between batches. Maximum final retained growth was **352 bytes**, including
measurement bookkeeping. New snapshot and float-writer checks assert that the
native operation actually runs, rather than accidentally measuring a decline.

A real retention test found 9–12.5 KiB of temporary `items` attribute names in
CPython's bounded type cache. Owned snapshot references were released, but the
growth still violated the existing bound. The implementation now uses an owned
interned name with the public attribute-lookup API. The exact resource tests
then passed on every ABI; no memory threshold was raised. The evidence retains
the failed investigation alongside the corrected results.

An additional debug-allocator run against the rebuilt extension in the working
CPython **3.14.9** environment passed **3,174** serde and benchmark-driver checks,
with 24 skips and five resource cases deselected.
An explicit resource run on that same 3.14.9 build then passed four cases with
the expected 3.12-only normalization skip.
The complete portable source
tree, without the extension, passed **2,789** checks on CPython 3.13.16 with nine
optional-module skips. See [working-tree checks](jsonx-second-root-checks.json),
[JUnit output](jsonx-second-root-3149.xml) and
[portable evidence](jsonx-second-portable.json).

## Retained implementation boundaries

The float writer uses CPython's formatter and a four-slot operation-local cache
keyed by exact float bits. String spellings retain owned exact-string references
only during that operation. Admission and writing share a **262,144-value**
budget, including repeated aliases; depth remains at most **16**. Private output
is capped at **1 MiB**, strings at 256 code points, and integers must be compact
before entering the writer. Unsupported tails discard the entire private
buffer. No Python callbacks, tracked allocations, signal checks or GIL release
occur while container references are borrowed. There is no cross-call result
cache. The original private `encode` helper retains its 65,536-value budget.

Strict snapshots own live iterator items and keys across allocation. Every
partial output and the collector holding those outputs remain untracked until
the whole tree validates. Shared acyclic branches receive independent copies.
The wrapper checks the original Python function, code, globals, relevant builtin
bindings, finiteness check, observers and recursion headroom before and after
copying. Declines use the original Python walker. Errors remain distinct from
declines, including when a callback replaces the walker during an allocation
failure. The facade resolves the original identity encoder before copying and
still invokes that saved callable afterward.

The retained tests cover these boundaries:

- [Finite floats and cache eviction](../../../tests/suites/product/serde/parity/test_jsonx_repeated_float_native.py):
  4,096 IEEE patterns, signed zero, subnormals, spelling-cache replacement,
  Unicode/escape parity, late failures, bounded alias expansion and callback
  mutation around the native operation.
- [Owned strict snapshots](../../../tests/suites/product/serde/parity/test_jsonx_identity_native.py):
  graph privacy, live-source clearing, reentrancy, metadata and builtin changes,
  sweeps over 48 copy-allocation and 13 configuration-failure positions per ABI,
  and reference cleanup. Positive acceleration checks
  run in fresh interpreters so earlier profiling cannot silently deoptimize them.
- [Dispatch guarantees](../../../tests/suites/product/serde/parity/test_jsonx_identity_dispatch.py):
  encoder lookup order, observable Python fallback, custom finiteness checks and
  backend configuration objects without added truth-testing hooks.
- [Memory plateaus](../../../tests/suites/product/serde/parity/test_jsonx_native_memory.py):
  existing buffer/normalization soaks plus actual snapshot and writer retention.
- [Retained isolated audit](../../../tests/tooling/runtime/json_identity_gc.py):
  forced collection at allocation/ownership boundaries and deterministic
  allocation/insertion failures, including metadata capture. Production sources
  are copied and never instrumented in place.

## Scope

The local sanitizer campaign instruments this extension and its headers on
macOS arm64. CPython and third-party backends were not rebuilt with sanitizers.
Process-lifetime leak detection is disabled, matching the existing CI policy;
operation-local retention is checked separately. The results qualify the tested
sources and environments, rather than proving every platform free of defects.

Ruff and formatting checks pass. Focused strict mypy checks pass for the two
changed product modules. The new benchmark driver passes its focused mypy check
with imported modules followed silently; this does not claim the complete
product/harness type gate passes in the minimal isolated environment.
