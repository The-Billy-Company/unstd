# Rand backend qualification

Measured October 25, 2026. The explicit `ints(..., seeds=...)` path exceeds
100× for Consumer-shaped batches of independently seeded integer counts on both
tested systems. **This does not establish a 100× complete Consumer workflow gain.**
Existing consumers keep their historical streams; the new path requires an
explicit caller opt-in and produces different values. No Consumer application
files were changed, and no application-specific logic is present in rand.

## Keyed counts: the qualified 100× scope

The captured Evaluation consumers construct `Generator(seed, backend="stdlib")`
and call `randint(1, 4)` separately for each deterministic, BLAKE2b-derived key.
The new generic API batches these independent counts:

```python
stream = rand.Generator(173)
counts = stream.ints(len(keys), 1, 4, endpoint=True, seeds=keys)
```

SplitMix64-v1 defines the new per-key stream. Repeated keys repeat values;
reordering/chunking preserves each key's result. Both ordinary RNG states stay
untouched. The implementation validates all keys and allocates every output on
each call. It keeps no result cache. The native kernel needs no NumPy dependency;
the portable Python implementation returns the same values without a speed claim.

Final revalidation after the helper relocation and final strict wheel build
uses the exact current source and selected native binaries:

| Fresh 8,000-key batch | macOS | Linux |
| --- | ---: | ---: |
| Median paired wall speedup | **346.8×** | **316.5×** |
| Median paired CPU speedup | **343.2×** | **316.0×** |
| Smallest wall pair | 289.4× | 114.5× |
| Smallest CPU pair | 292.2× | 113.2× |

All nine pairs on both clocks clear 100× on each system. The retained
[final proof driver](qualify-keyed.py.txt) uses the same checked phase/timing
functions as the comprehensive run. Final evidence:
[macOS](keyed-release-macos-py314.json),
[Linux](keyed-release-linux-py314.json).

The broader matrix below also uses nine adjacent, alternating timing pairs and
median matched before/after ratios. Wall and calling-thread CPU clocks are retained separately.
Fresh rows include constructing one new batch `Generator` per call, as well as
all original per-key construction, input validation and output allocation.
Reused rows use an already-created batch generator. Input key hashing is outside
both count-phase timers; the complete-consumer tests include hashing.

| Count batch | macOS wall / CPU speedup | Linux wall / CPU speedup |
| --- | ---: | ---: |
| 24 keys, reused generator | 244.8× / 237.2× | 188.0× / 187.9× |
| 24 keys, fresh generator | 22.7× / 22.7× | 21.6× / 21.6× |
| 371 keys, reused generator | 345.5× / 352.1× | 329.1× / 328.4× |
| 371 keys, fresh generator | 196.3× / 192.4× | 169.7× / 169.3× |
| 8,000 keys, reused generator | 362.8× / 361.1× | 277.5× / 277.2× |
| 8,000 keys, fresh generator | **357.9× / 345.1×** | **306.7× / 306.0×** |

In the comprehensive run, every one of the nine **8,000-key fresh-generator**
pairs also exceeds 100× on both clocks and both systems. Its smallest
wall/CPU ratios are 242.7×/274.6× on macOS and 257.7×/257.4× on Linux. Independent
latency medians are about 46.2 ms → 131 µs on macOS and 41.8 ms → 136 µs on Linux;
ratios above use matched pairs rather than ratios of independent medians.

The 371-key fresh case has one macOS wall pair at **76.8×**, although every CPU
pair exceeds 158× and its wall median is 196×. The 24-key fresh case misses 100×.
These failures remain in the reports.

Raw evidence: [macOS](keyed-macos-wheel-py314.json),
[Linux](keyed-linux-py314.json). Each retains source/helper/native-binary hashes,
captured consumer receipts, raw samples, histograms, per-case target results,
determinism/reordering checks and ordinary-state isolation.

## Complete consumers and unchanged streams

The full prospective distractor migration preserves the captured sorting,
hashing, sibling ordering and specification creation. Only the count expression
uses a batch lookup; deriving batch keys and constructing the lookup are included.
At 371 rows, complete consumers improve only **1.07–1.10×** across the two systems.
The 24-row engine case is 0.90× on macOS and 0.94× on Linux. They fail 100×.
Fixture messages contain one user turn; the synthetic batch-key extractor uses
that turn, whereas real consumers select the last user turn.

The compatible native Fisher–Yates kernel accelerates actual captured trim/split
consumers without changing any permutation, item identity or subsequent RNG state:

| Captured consumer | macOS wall / CPU speedup | Linux wall / CPU speedup |
| --- | ---: | ---: |
| Trim, 256 rows | 3.32× / 3.19× | 2.23× / 2.23× |
| Trim, 8,192 rows | 2.78× / 2.78× | 2.86× / 2.85× |
| Per-tool split, 256 rows | 2.20× / 2.18× | 1.66× / 1.65× |
| Per-tool split, 8,192 rows | 2.22× / 2.21× | 1.97× / 1.97× |

The native route admits ordinary `Generator.shuffle` lists and the unchanged
stdlib bit engine. Custom engines, per-instance/class hooks and custom sequences
retain stdlib behavior. Module scalar aliases and the secrets-backed crypto
surface stay unchanged. Bulk fallback draws hoist invariant work; NumPy unit
floats use its direct `random` kernel. At 4,096 values, stdlib floats improve
1.51×/2.03× and ints 1.69×/1.75× on Mac/Linux; one NumPy unit float improves
about 2.02× on both. Large NumPy floats remain close to their existing throughput.

Other consumer shapes stay around parity, with visible slow pairs and some
median regressions. For example, macOS masking-256 is 0.95× and scaled NumPy
floats of size one are 0.91×; Linux masking-8192 is 0.97×. Unchanged controls also
vary under the shared host's load. No universal no-regression claim is made.

Exact consumer evidence: [macOS](consumer-macos-py314.json),
[Linux](consumer-linux-py314.json). Backend matrix:
[macOS](backend-macos-py314.json), [Linux](backend-linux-py314.json).
All exact cases compare complete outputs and full RNG states outside timing.

The alternative of using the existing `sample` API for sparse corpus trimming is
reported separately: at 8,000 rows and hypothetical 1% retention it reaches
51.5× on macOS and 82.8× on Linux, below 100×. Retention fractions are prospective
scenarios, not observed production trim ratios. See
[macOS](strategies-macos-py314.json) and [Linux](strategies-linux-py314.json).

## Scope, provenance and validation

These are synchronous consumer slices over synthetic inputs, with lightweight
models projecting only the fields read by the captured functions. They exclude
LLM, network, database and unrelated application work. Sizes are grounded in
the 8,000-example corpus configuration, 371-tool catalogue and 24-example setting;
receipts distinguish configured sizes from hypothetical scenarios. Captured
source/AST hashes and relevant application test/configuration hashes live in
the [consumer fixtures](../../workloads/consumer_rand.json),
[keyed fixtures](../../workloads/rand_keyed.json) and
[sampling fixtures](../../workloads/rand_strategies.json).

macOS measurements use native arm64, macOS 26.6, CPython 3.14.9 and NumPy 2.5.3.
Linux measurements use native aarch64 CPython 3.14.8, glibc 2.41 and NumPy 2.5.3
inside a dedicated Docker Desktop Linux VM on the same Mac. This is Linux kernel
and compiler coverage, not independent physical hardware or an x86_64 production
server qualification. Measurements ran sequentially; the host also serves other
work, so every raw pair remains available. Finite measurements establish these
tested workloads and environments, not universal speed guarantees.

The archived [before wrapper](before-draw.py.txt),
[measured current wrapper](measured-draw.py.txt) and
[measured keyed helper](measured-keyed.py.txt) retain the measured bytes.
A concurrent repository organization moved the helper to `rand/keyed.py` after
the comprehensive timing. The final wrapper differs only in its top-level import; all other AST
nodes match. The helper's executable code and native C sources are identical;
the final helper also declares an empty export list. The
[qualification receipt](qualification.json) records this equivalence, final
source/binary hashes and the strict 8,000-key all-pairs gate.

Validation includes exact seeded histories for all four supported NumPy engines,
stdlib arbitrary-width integers, custom arithmetic/engine hooks, input failures,
reference lifetimes, signal cancellation and GC inspection during native output
construction. Keyed tests pin published SplitMix64 words, arbitrary-width
rejection, full int64 bounds, both implementations and state isolation.

- macOS Python 3.12: **538 passed**, debug allocator — [receipt](qualification-macos-py312.json), [log](tests-macos-py312.txt).
- macOS Python 3.13: **538 passed**, debug allocator — [receipt](qualification-macos-py313.json), [log](tests-macos-py313.txt).
- macOS Python 3.14: **630 passed**, including API/build/harness gates — [log](tests-macos-py314.txt).
- Linux Python 3.14: **557 passed**, including harness gates — [receipt](qualification-linux-py314.json), [log](tests-linux-py314.txt).
- Strict macOS native wheel build and installed-wheel product/security tests:
  **538 passed**. The measured Mac binary comes from the strict wheel build.
- Scoped Ruff, formatting and strict Mypy checks passed.

Initial Mac runs are retained as `*-initial-py314.json`; the pre-wheel keyed
run is `keyed-macos-py314.json`. Final timing followed refinement of the NumPy
non-unit guard, formatting and selection of the shipped wheel binary. No raw
timing pair was deleted to satisfy a target. The first strict build encountered
a concurrently missing compression source; subsequent strict builds succeeded.

## Reproduce

From the repository root, after rebuilding native extensions:

```console
uv run --no-sync python bench/profiles/rand_backend.py --compare-source bench/reports/rand/before-draw.py.txt --rounds 9 --batch-ms 3 --output /tmp/rand-backend.json
uv run --no-sync python bench/profiles/consumer_rand.py --compare-source bench/reports/rand/before-draw.py.txt --require-native --rounds 9 --batch-ms 3 --min-speedup 100 --output /tmp/consumer-rand.json
uv run --no-sync python bench/profiles/rand_keyed.py --compare-source bench/reports/rand/before-draw.py.txt --require-native --rounds 9 --batch-ms 3 --min-speedup 100 --output /tmp/rand-keyed.json
uv run --no-sync python bench/profiles/rand_strategies.py --compare-source bench/reports/rand/before-draw.py.txt --rounds 9 --batch-ms 3 --output /tmp/rand-strategies.json
```

The consumer/keyed/sampling drivers save their entire reports before exiting
unsuccessfully when any requested 100× case fails. Those overall failures are
expected here: only the qualified batch-count scope clears that target.
