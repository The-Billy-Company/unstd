# Sorted container kernels

`src/adapter.c` implements the optional `unstd.containers._sorted_native`
extension with the public CPython C API on Python 3.12–3.14. It contains no
platform-specific instructions or operating-system interfaces.

`install(cls, base, keyed=False)` installs native descriptors on unstd's own
Python subclasses. Upstream `sortedcontainers` classes remain unchanged.
Keyless classes receive insertion, left/right rank lookup, positional-index
translation and block/index maintenance. Keyed classes receive positional-index
translation while retaining upstream key extraction and block maintenance.
`KERNEL_VERSION` identifies the implementation in benchmark receipts.
The Python facade enables these layout-dependent kernels only with the verified
`sortedcontainers` 2.4.0 release. Other versions retain their upstream classes.

Methods inherited by arbitrary further subclasses delegate to the captured
upstream method. Ordinary exact instances use exact Python lists and integers
from their instance state. Unsupported state falls back before native changes;
invalid state produced by a comparison raises through checked list operations.
Comparisons retain Python's operand ordering and exceptions. Temporary items are
owned across comparison callbacks, and no list-storage pointer survives a
callback. Native methods keep dynamic dispatch to `_expand`, `_loc` and
`_build_index` so instance/class helper overrides remain effective.

Memory qualification covers dictionary replacement and finalizers during
comparison, active-call kernel reinstallation, reclaimed ephemeral types,
malformed internal state, and 24,000 mixed operations with small block loads.
The [recorded qualification](../../bench/reports/containers/README.md) includes
ASan/UBSan runs and Python's debug allocator on Python 3.12–3.14.

Build this extension with `-O1 -g -fsanitize=address,undefined
-fno-omit-frame-pointer` and link with `-fsanitize=address,undefined` for Linux
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
