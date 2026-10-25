# Clone memory audits

`force_gc.h` wraps allocation and memo-publication boundaries in a validation
copy of the native kernel. Production builds never include it. The header
forces collection before wrapped calls and marks the active phase and source
on `sys`, allowing the reentrant tests to mutate a live source at a verified
native allocation boundary.

`sys._unstd_clone_fault_after` instead selects a one-shot `MemoryError`; an
optional `sys._unstd_clone_fault_phase` selects the allocation family. The
campaign checks partial-output collection, retry independence, unchanged
source data and balanced recursion reservations. Phases 8 and 11 are available
for implementation changes but are currently unused by the kernel.

Build using the audited interpreter:

```console
python -m tests.tooling.runtime.clone_memory --force-gc --output /tmp/clone-gc
/tmp/clone-gc/python -m pytest tests/suites/product/clone/unit/test_reentrant_safety.py tests/suites/product/clone/unit/test_memo_safety.py
python -m tests.tooling.runtime.clone_memory --sanitize --output /tmp/clone-asan
/tmp/clone-asan/python -m pytest tests/suites/product/clone
```

Combine `--force-gc --sanitize` to instrument the forced-interleaving and OOM
campaign with AddressSanitizer and UndefinedBehaviorSanitizer. The generated
wrapper preserves the overlay and sanitizer runtime in clean-environment
subprocesses. Its manifest records source and binary hashes, compiler arguments,
a successful public native walk, and verified child-process module provenance.
On Linux, the sanitizer bootstrap removes inherited `LD_PRELOAD` after ASan has
loaded into Python. The wrapper restores it at each Python exec, keeping both
Python and the kernel instrumented while avoiding sanitizer injection into the
intermediate shell and `env` launcher processes.
