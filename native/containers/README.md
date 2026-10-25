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

Methods inherited by arbitrary further subclasses delegate to the captured
upstream method. Ordinary exact instances use exact Python lists and integers
from their instance state. Unsupported state falls back before native changes;
invalid state produced by a comparison raises through checked list operations.
Comparisons retain Python's operand ordering and exceptions. Temporary items are
owned across comparison callbacks, and no list-storage pointer survives a
callback. Native methods keep dynamic dispatch to `_expand`, `_loc` and
`_build_index` so instance/class helper overrides remain effective.
