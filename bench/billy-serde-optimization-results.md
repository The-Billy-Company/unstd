# Billy serde optimization measurements

**Status: the strict comparison with 2.1.1 still fails.** Source decoding is
0.29% slower in the original workload and 0.58% slower through a warmed direct
call. Nine of eleven equivalent-output cases pass the median-ratio gate. The
comparison with 3.0.0 passes all thirteen selected cases. The strict
zero-regression target remains unmet.
Release remains on hold.

Measured on 2026-10-19 using Billy's existing fixtures and public calls, without
Billy code changes. The implementation retains the 3.0 JSON values, output
format and call signatures. These results do not establish zero slowdown.

## Compared with 2.1.1

Each operation uses 105 neighboring before/after CPU batch pairs in one
interpreter. Times are microseconds per complete public call. The ratio is the
median of neighboring batch ratios; times are separately summarized medians,
so dividing displayed times need not reproduce the paired ratio exactly.

| Saved Billy operation | 2.1.1 µs | Current µs | Paired current / old | Faster pairs |
| --- | ---: | ---: | ---: | ---: |
| Compact source encoding | 1.905 | 1.890 | 0.98989 | 99 / 105 |
| Small request encoding | 0.106 | 0.080 | 0.75553 | 105 / 105 |
| Small request decoding | 0.124 | 0.095 | 0.75919 | 105 / 105 |
| Source decoding | 4.752 | 4.765 | **1.00294 — fails** | 33 / 105 |
| NDJSON writing | 5.836 | 5.208 | 0.89325 | 105 / 105 |
| NDJSON reading | 8.858 | 7.650 | 0.86573 | 105 / 105 |
| Source decoding, direct call | 4.734 | 4.753 | **1.00575 — fails** | 14 / 105 |

The original benchmark uses `functools.partial` for several operations. Additional
warmed direct-call companions exercise Python's specialized call instructions.
Every equivalent-output companion except source decoding also passes. Direct
compact encoding has a paired ratio of 0.98931, with 101 / 105 faster pairs.

All thirteen current cases match their independent references and required
native probes. The remaining failures are performance failures. The report retains
every batch sample, per-repetition result and failure:
[`billy-serde-optimized-legacy-results.json`](billy-serde-optimized-legacy-results.json).

2.1.1 returned cached orjson results without checking precision; the current
route walks the 103-value tree (seven containers and 96 strings, excluding keys)
because other inputs can contain integers converted to floats or differently
rounded float literals. Further material improvement likely needs precision
information from the backend or faster exact parsing; this is an inference from
the remaining cost.

## Default JSON format remains a separate cost

The default source encoder produces different bytes in 2.1.1: 17,525 compact
bytes, compared with the 17,628 bytes required by the 3.0 stdlib-compatible
format. Both outputs decode to the same values. The measured complete call costs
2.754 µs before and 21.750 µs currently. This operation is slower and receives
no equivalent-output release ratio. The strict gate covers the eleven cases
whose outputs match their references in both versions.

## Compared with 3.0.0

This separate run used the same frozen source, native binary, comparison runner,
original benchmark and fixture hashes as the legacy comparison. Each row has its
own 105 neighboring batch pairs; absolute times are not mixed between runs.

| Saved Billy operation | 3.0.0 µs | Current µs | Paired speedup |
| --- | ---: | ---: | ---: |
| Compact source encoding | 35.195 | 1.976 | 17.63× |
| Small request encoding | 0.646 | 0.090 | 6.81× |
| Small request decoding | 0.518 | 0.103 | 5.07× |
| Source decoding | 8.591 | 4.968 | 1.73× |
| NDJSON writing | 35.360 | 5.583 | 6.42× |
| NDJSON reading | 29.058 | 8.443 | 3.42× |

All thirteen selected cases pass the strict 1.0 ratio gate, exact output checks
and required native probes. The complete report is
[`billy-serde-optimized-results.json`](billy-serde-optimized-results.json).

## Method and native evidence

[`billy_serde.py`](billy_serde.py) imports Billy's original workload construction,
fixtures, independent references and CPU batch timer. Old and current module
graphs stay distinct while their retained callables alternate within the same
process. Fifteen batches across seven repetitions produce 105 neighboring pairs
per case; batch order alternates and garbage collection is disabled during
timing, then restored. These are saved-fixture CPU costs, not production latency.

Profiling observes actual Python-to-C entries. Native function dispatch restores
ordinary Python frames while profiling, so its observed decoder backend can be
msgspec even when the timed native dispatcher selects orjson. Separate untimed
checks attest the installed function binding and readiness after one
output-checked public warm call. An explicit native encoding probe also verifies
that the request fixture is accepted and matches its independent reference.
Nested orjson C calls are never invented as profiling events.

The runner records source hashes for Python, stubs, C, headers and native
binaries, plus the original benchmark, comparison runner and fixture hashes.
Changes during measurement invalidate the report. The measured CP314 binary
came from the actual Hatch wheel, rather than a separately configured prototype.

## Environment and reproduction

- macOS 26.6, arm64; CPython 3.14.6.
- orjson 3.13.0; msgspec 0.22.0; pybase64 1.5.0.
- Native module SHA-256:
  `7986b5de5198ac2fb30c431c3ee6e5484b0e0a4b298d5f4fe1f2b6c934f3fcf8`.
- Installed distribution metadata reports unstd 3.0.0; the selected source and
  binary hashes identify the current implementation actually measured.

Build the native wheel with the consumer's CPython version and a working
compiler/SDK, and select its matching extension in `src` before measuring.
The measured extension was extracted atomically so existing macOS processes
could retain their earlier loaded mappings.

```console
mkdir -p .local/billy-serde-v2 .local/billy-serde-v3
git archive v2.1.1 src/unstd | tar -x -C .local/billy-serde-v2
git archive v3.0.0 src/unstd | tar -x -C .local/billy-serde-v3
../billy/.venv/bin/python bench/billy_serde.py --paired --baseline-source .local/billy-serde-v2/src --runs 15 --repetitions 7 --select json- --select ndjson- --max-regression 1.0 --check --output bench/billy-serde-optimized-legacy-results.json
../billy/.venv/bin/python bench/billy_serde.py --paired --baseline-source .local/billy-serde-v3/src --runs 15 --repetitions 7 --select json- --select ndjson- --max-regression 1.0 --check --output bench/billy-serde-optimized-results.json
```

The legacy command exits unsuccessfully for both source-decoding cases.
The 3.0.0 command succeeds. Both runs were performed once after the final freeze;
no favorable rerun was selected.

## Validation and remaining limits

The actual Hatch native artifacts passed the serde suite on CPython 3.12
(2,685 tests, 38 skips), 3.13 (2,686 tests, 37 skips) and 3.14 (2,723 tests).
The final full all-extras repository run on Python 3.14 passed 6,062 tests with
six skips and 250 deselections in 84.23 seconds. Ruff, formatting, mypy and suite
inventory checks also passed.
Remote CI for [code commit 80fbdaf](https://github.com/The-Billy-Company/unstd/commit/80fbdaf)
completed all 56 checks successfully.

Near C-stack exhaustion, failure depth can depend on the invocation context:
a 256 KiB stack probe found a difference of a few caller levels. Identical
resource-exhaustion depth is not established. Native dispatch also defers to
ordinary Python behavior for tracing, profiling, monitoring and custom frame
evaluators.

Earlier reports, including the
[original release regression reproduction](billy-serde-release-results.json),
describe their own recorded source revisions and must not be combined with this
revision's timings.
