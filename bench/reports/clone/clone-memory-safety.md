# Clone memory safety validation

The final native source (`46f14a52…`) and Python dispatch (`958c0265…`) passed
the full clone suite on CPython 3.12, 3.13 and 3.14. The native kernel also
passed ASan/UBSan checks with forced collection and allocation failures.

| Run | Current checks verified | Skipped |
| --- | ---: | ---: |
| CPython 3.12, full clone suite | 441 | 28 |
| CPython 3.13, release clone and benchmark gates | 712 | 72 |
| CPython 3.14, release clone and benchmark gates | 726 | 58 |
| CPython 3.13, full clone suite with ASan/UBSan | 441 | 28 |
| CPython 3.13, forced GC and allocation failures with ASan/UBSan | 363 | 0 |
| CPython 3.14, forced GC and allocation failures with ASan/UBSan | 362 | 1 |

The forced totals combine 362/361 passing cases from the broad campaigns with
one corrected class-lifetime regression rerun under each identical binary.
That test originally assumed the private helper's callback received the whole
state. The continuation now delegates at the unsupported field. Its corrected
test checks that exact field and retains source/twin class replacement,
allocator churn and weak-cache release assertions. No production code changed
after the campaigns, and no failures remain in the current tests.

Two additional Python/native checks passed separately on both 3.13 and 3.14:
40 distinct record classes exceed the 32-entry native recipe cache, while a
memo collision changes the 33rd source's class. Repeated record/field aliases,
independent mutable data and the already selected reconstruction handler remain
correct across that cache handoff.

The existing replaced-allocator test was also strengthened after the campaigns:
it now warms the standalone helper directly and proves that its weak cache was
populated before replacing `__new__`. The strengthened test passed ordinary
3.12, ASan/UBSan 3.13 and forced-GC ASan/UBSan 3.13/3.14. Runtime sources and
instrumented binaries remained unchanged.

The forced runs exercise live source iteration, initialized published list/dict
prefixes, mutation of the actual memo, failed cyclic-output collection, class
and protocol changes during allocation/admission, weak-cache lifetime and
recursion accounting under errors and changing limits. Ten allocation families
receive one-shot failures at nine positions, followed by source-integrity,
cleanup, recursion-balance and successful-retry checks. The 3.14 skip is a
C-stack-pressure check specific to 3.12/3.13.

Each overlay verifies a successful public native walk and proves that a child
started with a clean environment loads the same instrumented extension. The
sanitizers cover the native extension; Python and third-party extensions remain
uninstrumented. LeakSanitizer is disabled, with weakref and explicit-collection
checks covering the relevant partial graphs. Forced-GC campaigns target eight
safety files; the full clone suite runs under ordinary ASan/UBSan on 3.13.

[Structured evidence](clone-memory-safety.json) records complete source/binary
hashes, compiler arguments, audit scope and limitations. The
[audit instructions](../../../tests/suites/product/clone/support/README.md)
build isolated kernels without replacing production extensions.
