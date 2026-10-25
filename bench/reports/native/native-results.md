# Bundled C kernel audit

Three measured Python hotspots now have optional C acceleration: structural
container cloning, exact-string Levenshtein distance, and Jaro matching. The
public Python functions and signatures remain in place. C fits the existing
extension build and these CPython loops. This initial audit did not compare Rust;
the later [Rust text experiment](../text/rust-text-results.md) measures C against an
opt-in Rust prototype and retains C by default because the prototype adds overhead.

Measurements were collected on 2026-10-21, macOS arm64, CPython 3.13.14, against
Git revision `035c166b579557f2977ebf7335ed0207d27ce75b`. The complete harness, source and
extension hashes, installed/base public signatures, Python/C profiles, route checks, and raw
timing samples are in [native-results.json](native-results.json).

## Measured public-call costs

Nine calibrated rounds per callable, three paired repetitions, alternating
before/after order. Each table value is the median of the three minimum batch
costs. Payload creation, compatibility checking and profiling are outside timing.
These compare the previous implementation with the current implementation;
they are not ratios against `copy.deepcopy` or RapidFuzz.

| Workload | Before | After | Before/after |
| --- | ---: | ---: | ---: |
| `clone.deep`, small scalar dictionary | 0.157 µs | 0.071 µs | 2.20× |
| `clone.deep`, 200 nested records | 60.023 µs | 48.487 µs | 1.24× |
| `clone.deep`, 500 flat records | 79.858 µs | 28.650 µs | 2.79× |
| `clone.deep`, custom copy fallback | 0.557 µs | 0.611 µs | 0.91× |
| `fuzz.distance`, 12 Unicode code points | 3.772 µs | 0.148 µs | 25.45× |
| `fuzz.distance`, 64 Unicode code points | 24.819 µs | 0.487 µs | 50.97× |
| `fuzz.distance`, 65 Unicode code points | 24.493 µs | 0.776 µs | 31.56× |
| `fuzz.distance`, 256 Unicode code points | 118.152 µs | 3.287 µs | 35.95× |
| `fuzz.distance`, 1,024 Unicode code points | 669.504 µs | 20.188 µs | 33.16× |
| `fuzz.jaro_winkler`, short name | 1.523 µs | 0.331 µs | 4.61× |
| `fuzz.jaro_winkler`, 75 Unicode code points | 32.121 µs | 1.247 µs | 25.77× |

The text measurements explicitly select the **base-install scorers**. An
installed RapidFuzz backend keeps its existing direct bindings and is not being
replaced by these kernels. Sequence and string-subclass fallback cases measured
approximately 1×. Mixed custom cloning pays about 54 ns of additional eligibility
work in the recorded case, a roughly 10% cost increase; native eligibility cannot be
assumed for arbitrary objects. Cloning is most useful for ordinary data trees.

Several clone, text and fallback batches exceeded the harness's 25% noise
threshold; all spreads and samples are retained. The 200-record clone and
256-character distance cases stayed below 20%. These are local observations, with no new universal
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
tests cover source containers as well as atom leaves. This follows CPython's
[borrowed-reference guidance](https://docs.python.org/3.13/extending/extending.html#thin-ice)
on implicit Python calls invalidating borrowed container items.

Native stack-guard exhaustion returns `NotImplemented` to the Python walker.
Propagating that native-only `RecursionError` would select whole-graph
`deepcopy`, which could preserve shared references in an acyclic tree where
the original Python walker still succeeds. A bounded C-recursion-pressure
regression pins the distinction and fails against the earlier native code.

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

- Full final all-extra suite (`--require-extras`): **6,300 passed, 62 skipped,
  250 deselected**.
- Final native clone/text and profile gates on Python 3.13.14: **234 passed**.
- Python 3.12.13 clone/text suites: **204 passed, 11 optional-backend skips**.
  Python 3.14.6 text: **145 passed, 3 optional-backend skips**; Python 3.14.8
  clone: **58 passed, 9 skips**. The C-counter-specific regression skips 3.14,
  which uses a machine-stack guard. All other new native tests ran.
- Final clone/text ASan + UBSan run: **215 passed**, with no reported memory or
  undefined-behavior findings. Leak detection was disabled for process-lifetime
  Python/runtime allocations; owned-source reference balance is tested directly.
  On macOS the preload variable was restored inside the instrumented parent so
  test subprocesses inherited the sanitizer runtime at startup.
- An additional UBSan build passed **20,000 seeded differential text pairs**
  against an independent edit-distance matrix and exact portable Jaro scores,
  including maximum-work and Unicode storage boundaries.
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

Save the Python-only comparison revision's `src/` directory, build the current extensions,
then run:

```console
python bench/native_profile.py --compare-source /tmp/before/src --rounds 9 --repetitions 3 --output /tmp/native-results.json
python -m pytest tests/suites/product/clone tests/suites/product/text tests/suites/assurance/gates/bench/test_native_profile.py -q
```

The profile fails if an expected C kernel is missing, binds outside the selected
source tree, changes an installed or base public signature/binding, returns an
incompatible result, or silently takes an unexpected native/fallback route.
Untimed adapters record each native return: a C call that returns
`NotImplemented` cannot count as completed acceleration. Instrumentation is
restored before timing, including after a failed proof. Native-enabled comparison
baselines are rejected so they cannot accidentally import the current kernels.
Unit tests enforce the evidence checks without asserting host-specific speedups.
