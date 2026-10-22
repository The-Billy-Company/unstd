# Bundled C kernel audit

Three measured Python hotspots now have optional C acceleration: structural
container cloning, exact-string Levenshtein distance, and Jaro matching. The
public Python functions and signatures remain in place. C fits the existing
extension build and these CPython loops; no Rust comparison was made, and the
language choice alone does not establish which implementation is fastest.

Measurements were collected on 2026-10-21, macOS arm64, CPython 3.13.14, against
Git revision `fedb4d0109488816fbba76614146d93e2f423780`. The complete source and
extension hashes, public signatures, Python/C profiles, route checks, and raw
timing samples are in [native-results.json](native-results.json).

## Measured public-call costs

Nine calibrated rounds per callable, three paired repetitions, alternating
before/after order. Each table value is the median of the three minimum batch
costs. Payload creation, compatibility checking and profiling are outside timing.
These compare the previous implementation with the current implementation;
they are not ratios against `copy.deepcopy` or RapidFuzz.

| Workload | Before | After | Before/after |
| --- | ---: | ---: | ---: |
| `clone.deep`, small scalar dictionary | 0.160 µs | 0.071 µs | 2.24× |
| `clone.deep`, 200 nested records | 59.994 µs | 47.713 µs | 1.26× |
| `clone.deep`, 500 flat records | 78.906 µs | 29.753 µs | 2.65× |
| `clone.deep`, custom copy fallback | 0.548 µs | 0.623 µs | 0.88× |
| `fuzz.distance`, 12 Unicode code points | 3.032 µs | 0.144 µs | 21.01× |
| `fuzz.distance`, 64 Unicode code points | 23.731 µs | 0.480 µs | 49.42× |
| `fuzz.distance`, 65 Unicode code points | 24.882 µs | 0.788 µs | 31.59× |
| `fuzz.distance`, 256 Unicode code points | 107.581 µs | 2.979 µs | 36.12× |
| `fuzz.distance`, 1,024 Unicode code points | 822.483 µs | 20.671 µs | 39.79× |
| `fuzz.jaro_winkler`, short name | 1.502 µs | 0.324 µs | 4.64× |
| `fuzz.jaro_winkler`, 75 Unicode code points | 27.674 µs | 1.240 µs | 22.32× |

The text measurements explicitly select the **base-install scorers**. An
installed RapidFuzz backend keeps its existing direct bindings and is not being
replaced by these kernels. Sequence and string-subclass fallback cases measured
approximately 1×. Mixed custom cloning pays about 75 ns of additional eligibility
work in the recorded case, a roughly 14% loss; native eligibility cannot be
assumed for arbitrary objects. Cloning is most useful for ordinary data trees.

Several text and fallback batches exceeded the harness's 25% noise threshold;
all spreads and samples are retained. The large clone cases and both name cases
were below that threshold. These are local observations, with no new universal
performance floor. The recorded cycle comparison remains a compatibility check,
not a claimed cycle acceleration.

## Compatibility and memory boundaries

`clone.deep` admits bounded exact `dict`/`list`/`tuple` trees, exact string keys,
and exact scalar builtin leaves. It preserves leaf identity, dictionary order,
new mutable branches and the deliberate duplication of shared branches. Other
trees retain the existing Python walker, including classes, reducers, sets,
foreign keys and cyclic fallback. `asdict`, `copy` and `deepcopy` keep their
existing implementations.

The clone proof allocates nothing on success, invokes no copy/hash/equality
hooks, and is bounded by 128 container levels and 65,536 visited values. The copy
pass independently revalidates values because allocation may run finalizers.
Real iterators and owned item references pin live container values across
allocations; shallow bulk copies are revalidated as owned snapshots. Both
recursive passes guard the native stack, preserve a conservative Python frame
budget, unwind partial outputs on refusal/error, and retain the GIL. Refcount
tests cover source containers as well as atom leaves.

Levenshtein uses checked arrays of unsigned 64-bit words, explicit multiword
carries and masked final words. It accepts at most 256 distinct pattern code points,
65,536 pattern code points, 262,144 text code points, and 4,194,304 word updates.
Jaro uses checked match buffers and a bounded pair-search budget. Both read
Unicode code points, including lone surrogates, from strongly held immutable
exact strings. Unsupported shapes or budgets select the original Python body;
allocation failures propagate. Winkler prefix arithmetic stays in Python, while
strict native floating-point flags preserve Jaro operation order.

The new modules have no mutable global state, support separate interpreter
instances with the GIL, and decline free-threaded builds. Builds without a
compiler retain the portable wheel. Each optional module builds independently;
release builds require all of them. Public API shapes and third-party runtime
dependencies are unchanged.

## Validation

- Full all-extra suite: **6,278 passed, 59 skipped, 250 deselected**. The final
  clone tuning was subsequently checked with the focused suites below.
- Final native clone/text and profile gates on Python 3.13: **212 passed**.
- Final clone/text suites on Python 3.12.13 and 3.14.6: **188 passed, 11 optional
  backend skips each**. All new native tests ran on both interpreters.
- Final clone/text ASan + UBSan run: **199 passed**, with no reported memory or
  undefined-behavior findings. Leak detection was disabled for process-lifetime
  Python/runtime allocations; owned-source reference balance is tested directly.
- Artifact tests: **51 passed**; installed-consumer checks: **77 passed**.
  Checks cover both wheel build routes, source rebuilds, dependency-free native
  consumers, compiler failure, independent partial builds and portable fallback.
- Ruff, formatting, strict mypy and suite inventory checks passed. CI now
  requires native imports on Python 3.12–3.14, runs the debug allocator, and
  builds instrumented extensions for Linux ASan/UBSan. Linux/Windows CI still
  needs to execute on those hosts.

Differential tests include independent edit-distance matrices, exhaustive short
strings, generated Unicode, 64-bit carry boundaries, colliding character hashes,
Jaro transpositions and exact float comparisons. Clone checks include type/order,
mutable independence, copy-hook mutation, source reference balance, recursion
pressure, collector stress, cycles, shared branches and traversal budgets. This
evidence does not constitute a formal proof of memory safety for every runtime.

## Reproduce

Save the comparison revision's `src/` directory, build the current extensions,
then run:

```console
python bench/native_profile.py --compare-source /tmp/before/src --rounds 9 --repetitions 3 --output /tmp/native-results.json
python -m pytest tests/suites/product/clone tests/suites/product/text tests/suites/assurance/gates/bench/test_native_profile.py -q
```

The profile fails if an expected C kernel is missing, binds outside the selected
source tree, changes a public signature, returns an incompatible result, or
silently takes an unexpected native/fallback route. Unit tests enforce the
evidence checks without asserting host-specific speedups.
