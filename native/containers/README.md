# Sorted container kernels

`src/adapter.c` implements the optional `unstd.containers._sorted_native`
extension with the public CPython C API on Python 3.12–3.14. It contains no
platform-specific instructions or operating-system interfaces.

`install(cls, base, keyed=False)` installs native descriptors on unstd's own
Python subclasses. Upstream `sortedcontainers` classes remain unchanged.
Keyless classes receive left/right rank lookup and positional-index translation.
Keyed classes receive positional-index translation while retaining upstream key
extraction. Keyless classes also receive eligible prefix/suffix slice deletion
and monotonic integer `update` kernels. Single-item insertion/removal, general
block maintenance and positional-index building remain upstream.
`KERNEL_VERSION` identifies the implementation in benchmark receipts; version 2
uses separate production/testing capsule namespaces so configuration layouts
cannot be mixed. The Python facade enables these layout-dependent kernels only
with verified `sortedcontainers` 2.4.0. Other versions retain upstream classes.

Methods inherited by arbitrary further subclasses delegate to the captured
upstream method. Exact instances use checked exact Python lists and integers
from their instance state. Rank queries use callback-free signed 64-bit integer
binary searches; unsupported values delegate the complete call before any
Python comparison. Generic reads, index building, string-subclass keyword
binding and nonstandard metadata retain upstream callbacks and finalizer timing.
The native code releases temporary state before entering those fallbacks.

## Mutations

Prefix/suffix slice deletion below the upstream rebuild threshold drops whole
blocks and delegates at most one partial boundary to upstream. Increasing
integer list/tuple batches below the update rebuild threshold stage replacement
tail blocks. The append kernel accepts at least 256 values and bounds staging
work relative to the existing block count and tail size. Overlapping, unsorted,
keyed, generic and small batches use upstream.

Preflight checks helper identities, class metadata, exact dictionary keys,
block geometry, length arithmetic and storage reference counts. Active
iterators or external aliases to internal arrays force fallback. Deleted whole
blocks contain only exact integers, so their release cannot invoke user
finalizers. Append snapshots hold owned references to payload values. Both
kernels stage new metadata before commit, then revalidate class/configuration,
helpers, fields, aliases and payload identity. Existing exact dictionary keys
are replaced while old state remains pinned; a failed replacement restores the
original metadata. No list-storage pointer crosses a callback or allocation.
The private `mutation_stats` counters prove actual fast-path use outside timing.
Successful batches invalidate the positional index. A subsequent query that
needs it rebuilds the index upstream; mutation-only timings exclude that later
query. Retaining the old index array forces the alias-preserving fallback.

Test builds define `UNSTD_CONTAINERS_TESTING=1`, exposing `_set_test_hook` only
there. Deterministic failpoints cover staging allocations and all four commit
replacements; a stage callback exercises invalidation and upstream fallback.
`UNSTD_CONTAINERS_REQUIRE_TEST_HOOKS=1` makes missing hooks fail qualification.
Production builds have no hook fields, exports or per-point checks.

Memory qualification covers dictionary replacement and finalizers during
comparison, active-call kernel reinstallation, reclaimed ephemeral types,
malformed internal state, and 24,000 mixed operations with small block loads.
The [mutation qualification](../../bench/reports/containers/mutations/README.md) includes
ASan/UBSan runs and Python's debug allocator on Python 3.12–3.14.

Build this extension with `-O1 -g -fsanitize=address,undefined
-fno-omit-frame-pointer -DUNSTD_CONTAINERS_TESTING=1` and link with `-fsanitize=address,undefined` for Linux
sanitizer runs. Keep an ordinary build in a separate source tree. The
[`qualify_memory.py`](qualify_memory.py) driver runs two disjoint partitions:
`--phase main` preloads ASan in the test interpreter, and `--phase children`
uses an ordinary launcher whose owned workers load the sanitized build. Both
retain the real worker deadlines, working directories and group cleanup.

This separation works around a reproduced GCC ASan/ARM64 runtime failure in
forked Python children before exec, including children that import no product
code. It does not suppress sanitizer failures. LeakSanitizer is disabled for
the uninstrumented Python/dependency runtime; weak-reference assertions check
native class and instance lifetimes directly.
