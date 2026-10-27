# Linear Jaro matching for repetitive text

The base-install `fuzz.jaro_winkler` now indexes character positions when both
exact input strings exceed 32 characters. The previous fallback repeatedly
searched past occurrences that had already matched, making repetitive strings
quadratic. Each indexed occurrence is now stored once and consumed at most once.
The input walk and final transposition scan are linear too, giving
O(len(a) + len(b)) time and temporary storage.

Both window boundaries advance monotonically. An occurrence left of the window
can never be used later; an occurrence beyond its right edge remains available.
Consuming the earliest eligible occurrence therefore preserves the original
greedy match order. The transposition calculation and floating-point operations
are unchanged. Native kernels run first, and inputs with a shorter side of at
most 32 characters retain the previous scan, whose work is bounded by that
constant. String subclasses and foreign sequences retain their observable hooks.

## Measured impact

October 27, 2026, macOS arm64, CPython 3.14.9. The
[raw report](jaro-results.json) retains every wall/CPU sample, independent
oracle scores, dispatch observations, fixture hashes and implementation receipts.
These are synthetic workloads on one machine, with RapidFuzz excluded from the
public dispatch. Installing RapidFuzz continues to select its existing scorer.

| Public base-install operation | Before | After | Paired speedup |
| --- | ---: | ---: | ---: |
| Jaro–Winkler, 10,000 repetitive characters, native available | 2.301 s | 2.550 ms | 885× |
| Same pair, native disabled | 1.712 s | 2.034 ms | 829× |
| 3,000 repetitive characters, native disabled | 191.3 ms | 0.758 ms | 246× |
| Complete `best_match`, three 3,000-character choices, native disabled | 338.2 ms | 1.914 ms | 177× |
| Seeded varied 3,000-character pair, native disabled | 8.622 ms | 0.818 ms | 11.5× |
| Mixed ASCII/Latin/CJK/astral pair, native disabled | 44.88 ms | 0.942 ms | 47.6× |

The 10,000-character fixture is `"a" * 10000` versus `"a" * 9999 + "b"`.
Both implementations return **0.99996**. With native available, instrumentation
confirms that both versions decline into Python for this workload: the gain
comes from the new algorithm, using the same compiled extension in both arms.
CPU time also improves: 2.036 s → 2.461 ms with native available, and
1.667 s → 2.011 ms without it. The gain is not solely a scheduling effect.

Candidate-only scaling increases the input tenfold, from 10,000 to 100,000
characters. Source-only latency rises from **2.065 ms to 20.464 ms** (9.91×).
The native-capable lane measures 2.451 ms → 25.653 ms. No baseline timing or
speedup is invented for the 100,000-character case.

## Controls and tradeoffs

This removes a worst-case scaling problem; some input shapes cost more.
Source-only `MARTHA`/`MARHTA` measured 2.397 → 2.399 µs, and
`Jane`/`John` measured 1.333 → 1.386 µs. Native short calls retain their original
kernel. Tiny queries against 100,000-character choices retain the scan; the
matching control measured 70.9 → 69.3 µs source-only. Both input orientations
and disjoint controls are included in the raw report.

A source-only pair of 1,000 distinct Unicode characters, in reversed order,
measured **228 → 318 µs**, a 39% increase in median latency. Its paired speedup
is 0.65×. The native-capable version retains its native route. Index construction
has overhead on diverse input with few repeated matches.

The index also uses more temporary memory. For 3,000 repeated characters,
`tracemalloc` peaks increase from **50,784 to 141,984 bytes** (2.80×). For the
diverse source-only control they increase from 43,048 to 212,200 bytes. Inputs
are prepared before tracing; these figures measure additional Python
allocations, excluding native malloc and interpreter/input storage. Storage
remains linear. Allocation tracing runs separately from timing.

## Verification and method

The complete text suite plus its public signature contract pass **453 tests**
(452 text contracts and one public API check). Regression/property cases cover greedy
window edges, expired/future occurrences, asymmetric lengths, repeated matches,
odd transposition cycles, NULs, lone surrogates, astral Unicode, subclasses,
unhashable foreign values and native absence/refusal. Public `best_match`,
`extract` and `cdist` preserve exact scores, inclusive cutoffs and tie ordering.
Existing property tests compare against an independent textbook window scan
and RapidFuzz. Scoped Ruff, formatting and strict mypy checks pass.

Independent review exhaustively compared 132,496 ternary-string pairs against
the original, bit-for-bit, while forcing the indexed path. A separate seeded
source-only check compared 5,000 Unicode pairs of lengths 33–128 at four prefix
weights (0, 0.1, 0.25 and 1): **20,000 exact score comparisons each** on
CPython 3.12.15, 3.13.17 and 3.14.9. Both optional backends were blocked during
these cross-version comparisons.

The [driver](../../profiles/text_jaro.py) loads two isolated copies of the public
base-install wrapper. Checks against RapidFuzz 3.14.6 run before timing, with a
2e-16 absolute tolerance for the independent oracle; saved/current scores must
agree exactly before and after timing. The report records the installed oracle
version. Seven rounds alternate arm order, calibrating each arm independently.
GC remains enabled. The table reports median latencies and median matched
speedup ratios, which need not equal the ratio of separate medians. Every
wall/CPU batch remains visible, including host-load variation. Checks, dispatch
instrumentation, calibration and memory tracing stay outside measured batches.
The driver saves results before enforcing a **100× minimum improvement** for
the repetitive 10,000-character case in both lanes; both guards pass.

The baseline is the unchanged `fuzz.py` from commit
`b3c5c57bf5fd445346c3cf54234da8995c366b9d`, verified byte-for-byte against its
Git object. Its SHA-256 is
`e3587d372fb1104100b0b3b7fa2b5a02c94a69e92a5ed0f3cc1955f13b7d0a97`.
The candidate SHA-256 is
`5127d7a5aa914e8e45d8a95e7a9bbcf2584ab97168d641cd46b729ac7827aae4`.
Both arms use the same native binary, hash
`355914ad0497767fdf4b061bc5c32a7eecd694caffc77f431b9d64444ff1d12b`.
The driver checks source/binary receipts again after timing.

The checkout contains other work being edited concurrently. A combined run of
the text suite and full public API gate finished with **510 passed, 3 failed**.
The failures are outside text: `unstd.dataset` moved a callable's module identity,
`unstd.serde.jsonx` added an export absent from its contract, and the shared
backend-unavailable check encountered the dataset identity mismatch. An earlier
run of that API gate passed all 61 tests before those concurrent edits. The suite
inventory and CI-group inventory checks pass. This report does not claim the
entire checkout passes every gate.

## Reproduce

With the repository development environment, RapidFuzz and the compiled text
extension installed, save the original wrapper and select the installed binary:

```sh
mkdir -p .local/jaro-before/unstd/text
git show b3c5c57bf5fd445346c3cf54234da8995c366b9d:src/unstd/text/fuzz.py > .local/jaro-before/unstd/text/fuzz.py
JARO_NATIVE=$(.venv/bin/python -c 'import unstd.text._text_native as n; print(n.__file__)')
.venv/bin/python bench/profiles/text_jaro.py \
  --baseline-source .local/jaro-before --candidate-source src \
  --baseline-native "$JARO_NATIVE" --candidate-native "$JARO_NATIVE" \
  --rounds 7 --output .local/jaro-results.json
.venv/bin/python -m pytest tests/suites/product/text \
  tests/suites/assurance/gates/packaging/test_public_api.py -q
```

No native source or public signature changed. Compiler-free builds use the same
portable algorithm, covered by the forced-portable lane.
