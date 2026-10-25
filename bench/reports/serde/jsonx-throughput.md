# JSONX public Python throughput qualification

This page retains the first-pass measurements. The
[second-pass comparison](jsonx-second-pass.md) measures the subsequent changes
against both this first pass and the original implementation, using the
unchanged existing Python fixtures.

**The 100× target was not achieved.** The measurements below call the public Python JSONX functions and include dispatch, eligibility checks, codec work, result allocation and text conversion. The existing benchmark fixtures and harnesses were left unchanged. Private C component probes were diagnostic only and do not supply any gain in this report.

Measured on 2026-10-25 on an Apple M5 Max, macOS 26.6 arm64, with orjson 3.12.0 and msgspec 0.22.0. Interpreters: CPython 3.12.15, 3.13.16 and 3.14.8. Baseline and candidate each had a native JSON extension built for the selected interpreter; installing a missing extension is not counted as an implementation gain.

## Existing benchmarks

The existing public-function registry uses 32 Unicode records. Its registered callables and correctness oracles were executed without fixture edits. Each row is the median across three alternating source repetitions of the minimum of five calibrated wall-clock batch means. Raw samples remain available alongside stdlib controls in the core harness.

| Existing registry workload | Python 3.12 before → after (µs) | Gain | Python 3.13 gain | Python 3.14 before → after (µs) | Gain |
|---|---:|---:|---:|---:|---:|
| `dumps` | 7.78 → 3.80 | 2.05× | 2.14× | 8.45 → 4.77 | 1.77× |
| `dumpb` | 1.31 → 1.20 | 1.09× | 0.99× | 1.31 → 1.50 | 0.87× |
| `loads` | 3.27 → 3.21 | 1.02× | 0.99× | 2.93 → 3.78 | 0.78× |
| `load` | 3.79 → 3.52 | 1.08× | 0.90× | 3.55 → 4.54 | 0.78× |
| `dump` | 32.58 → 30.76 | 1.06× | 1.06× | 34.08 → 37.72 | 0.90× |
| `canonical` | 8.12 → 8.53 | 0.95× | 1.09× | 9.55 → 11.21 | 0.85× |
| `canonical_strict` | 20.14 → 19.98 | 1.01× | 1.04× | 25.94 → 25.96 | 1.00× |
| `JSONEncoder` | 0.089 → 0.085 | 1.04× | 1.08× | 0.092 → 0.110 | 0.84× |
| `JSONDecoder` | 0.396 → 0.375 | 1.06× | 1.08× | 0.394 → 0.416 | 0.94× |

The unchanged `bench.profiles.serde_profile` also ran its full public workload set against both sources, with timing outside cProfile and Python function profiling in a separate pass. It has 118 workloads on Python 3.12 and 122 on 3.13/3.14 because the latter environments also include NumPy. Its repeated timings use the median of each source's per-repetition minimum batch mean:

| Existing function-profile workload | Python 3.12 gain | Python 3.13 gain | Python 3.14 gain |
|---|---:|---:|---:|
| `jsonx.dumps/small` | 1.18× | 1.29× | 1.22× |
| `jsonx.dumps/indent` | 3.05× | 3.08× | 3.34× |
| `jsonx.dumps/records` | 1.07× | 0.98× | 0.94× |
| `jsonx.dumpb/records` | 0.98× | 0.89× | 0.99× |
| `jsonx.loads/records` | 1.03× | 1.12× | 0.88× |
| `jsonx.canonical/default` | 0.95× | 1.03× | 1.07× |
| `jsonx.dump/stream` | 0.94× | 1.01× | 0.94× |

Python profiling retains per-function call counts, self time and cumulative time for both sources. In each ten-pass profile, `_internal.json_encode._snapshot` changes from 70 calls to zero: eligible indented calls now use the native proof instead of the Python snapshot. Profiling is diagnostic and can change native dispatch observability; profiler times are not used for throughput ratios.

The existing core benchmark registry was also audited and timed through `python -m bench --group serde --rounds 5 --json`. Its original JSONX workloads are mostly already accelerated or intentionally use stdlib fallback:

| Existing core workload | Python 3.12 gain | Python 3.13 gain | Python 3.14 gain |
|---|---:|---:|---:|
| jsonx.dumpb · 500 records | 1.01× | 1.06× | 0.91× |
| jsonx.dumps · 32-int record | 1.22× | 1.30× | 1.18× |
| jsonx.dumps · 500 records | 0.87× | 0.96× | 0.81× |
| jsonx.dumps · text payload | 1.48× | 1.28× | 1.48× |
| jsonx.loads · 1000 integers | 1.05× | 0.98× | 1.08× |
| jsonx.loads · 20 nested records | 0.98× | 1.06× | 1.01× |
| jsonx.loads · 500 records | 1.08× | 0.90× | 0.84× |

The core harness preserves stdlib control timings in every raw row. Gains on an unchanged route and losses that move with its stdlib control indicate host drift, rather than an established implementation change. All JSONX core rows met their existing ratio floors. Full Serde output also retains unrelated cases and any pre-existing floor failures.

The complete `bench.speeds run --group serde` command succeeded on Python 3.13 and 3.14. On 3.12 its full-registry setup requires unavailable NumPy, so its nine unchanged JSONX cases were selected from `_serde_cases()` and run through the existing `bench.speeds.timings` and correctness checks. The [before setup error](jsonx-existing-speeds-before-312.error.json), [after setup error](jsonx-existing-speeds-after-312.error.json), and isolated measurements are retained; this is not a complete 3.12 registry run.

## Additional public workload coverage

A separate output-checked driver adds wide trees, large strings, Unicode, float objects and indentation to reveal the previous eligibility cliffs. These are additional workloads, not replacements for the existing fixtures. Fifty workloads per interpreter matched independent stdlib outputs before and after timing, including result types, float bits and object key order.

| Additional workload | Python 3.12 before → after (µs) | Gain | Python 3.13 gain | Python 3.14 before → after (µs) | Gain |
|---|---:|---:|---:|---:|---:|
| 100,000 integers, compact bytes | 2,415.46 → 292.30 | 8.26× | 8.30× | 2,506.50 → 404.15 | 6.20× |
| 100,000 integers, default text | 2,277.02 → 745.35 | 3.05× | 3.22× | 2,536.35 → 1,082.17 | 2.34× |
| 100,000 integers, indent=2 | 10,152.33 → 436.96 | 23.23× | 5.07× | 2,715.79 → 633.85 | 4.28× |
| 1,000 ASCII records, indent=2 | 1,094.15 → 52.94 | 20.67× | 3.50× | 233.55 → 67.25 | 3.47× |
| 1,000 Unicode records, indent=2 | 1,108.46 → 128.26 | 8.64× | 1.55× | 253.99 → 158.49 | 1.60× |
| 1,000 float records, indent=2 | 1,495.74 → 473.87 | 3.16× | 1.05× | 498.82 → 457.69 | 1.09× |
| 1,000 Unicode records, default text | 280.50 → 149.51 | 1.88× | 1.90× | 340.34 → 180.60 | 1.88× |
| 10,000 float members, str decode | 499.80 → 488.85 | 1.02× | 1.09× | 1,408.86 → 763.62 | 1.84× |
| 10,000 float members, bytes decode | 463.79 → 502.53 | 0.92× | 1.10× | 1,466.43 → 636.99 | 2.30× |

The gains come from admitting wider builtin trees to native encoding, native two-space indentation, escaping Unicode output without a second serialization, and avoiding a redundant parse for float-bearing object documents. Float normalization for indentation is enabled on Python 3.12; newer interpreters retain the existing float fallback.

**Eligibility work remains bounded.** New wide proofs visit at most 262,144 values, counting repeated aliases. The original private guard retains its 65,536-value limit. Trees outside these limits use the existing fallback. This bounds added proof work before fallback, including for a small graph whose aliases imply a huge serialization. It does not bound the output or cost of stdlib serialization itself.

The additional driver uses three repetitions in alternating source order and five calibrated wall-clock batches per measurement. Its table uses the median across repetitions of the minimum batch mean. Raw reports retain wall and thread CPU samples, losses, stdlib controls, import paths, Python/native source hashes and binary hashes. Throughput counts encoded JSON bytes: output bytes for encoding and input bytes for decoding. Input construction and correctness checks are outside timing; the full public call and its result management are timed.

| Additional driver: slowest measured ratio | Workload | Unchanged canonical control ratio |
|---:|---|---:|
| Python 3.12.15: 0.85× | `loads/small/bytes` | 1.03× |
| Python 3.13.16: 0.87× | `loads/integers-10k/bytes` | 0.95× |
| Python 3.14.8: 0.88× | `dumpb/ascii-records-1k` | 0.97× |

Some batches exceeded 25% spread on this shared workstation. All measured losses remain in the reports; small differences require a quieter worker to interpret. Canonical, custom-hook and streamed behavior retain their existing routes. The measured gains do not establish a 100× improvement across JSONX operations.

The separate [safety qualification](jsonx-safety.md) and [native safety results](jsonx-native-safety.json) cover mutation, recursion, memory behavior and sanitizer checks.

## Raw evidence

| CPython | Existing Python timings + profiles | Existing core: before / after | Existing function registry: before / after | Additional 50-workload comparison |
|---|---|---|---|---|
| 3.12.15 | [profile](jsonx-existing-profile-312.json) | [before](jsonx-existing-core-before-312.json) / [after](jsonx-existing-core-after-312.json) | [before](jsonx-existing-speeds-isolated-before-312.json) / [after](jsonx-existing-speeds-isolated-after-312.json) | [comparison](jsonx-throughput-312.json) |
| 3.13.16 | [profile](jsonx-existing-profile-313.json) | [before](jsonx-existing-core-before-313.json) / [after](jsonx-existing-core-after-313.json) | [before](jsonx-existing-speeds-before-313.json) / [after](jsonx-existing-speeds-after-313.json) | [comparison](jsonx-throughput-313.json) |
| 3.14.8 | [profile](jsonx-existing-profile-314.json) | [before](jsonx-existing-core-before-314.json) / [after](jsonx-existing-core-after-314.json) | [before](jsonx-existing-speeds-before-314.json) / [after](jsonx-existing-speeds-after-314.json) | [comparison](jsonx-throughput-314.json) |

All three registry repetitions, their exact commands and source/native identities are indexed in the [evidence manifest](jsonx-evidence-manifest.json). The table links the first registry repetition; the manifest indexes every retained output.

## Reproduce

The baseline was saved from `94589856d2e604310b55020e40c51010f2af4755` before JSONX edits. Preserve `src/unstd` and `native/serde`, then build each source tree's native extension with the selected interpreter. Both trees must use the same optional backend versions. Source and native identities are retained in the comparison and registry reports.

Run the existing harnesses from the repository root, selecting the source through `PYTHONPATH` before importing `bench`:

```console
python -m bench.profiles.serde_profile \
  --compare-source /tmp/jsonx-throughput-baseline/src \
  --source /tmp/jsonx-throughput-final-bounded/src --rounds 3 --repetitions 3 \
  --output /tmp/jsonx-existing-profile.json
PYTHONPATH=/tmp/jsonx-throughput-final-bounded/src:. python -m bench --group serde --rounds 5 --json
PYTHONPATH=/tmp/jsonx-throughput-final-bounded/src:. python -m bench.speeds run --group serde \
  --rounds 5 --output /tmp/jsonx-existing-speeds.json
```

Repeat the two registry commands with the baseline source. The additional [public-call driver](../../profiles/jsonx_throughput.py) runs with:

```console
python -m bench.profiles.jsonx_throughput \
  --compare-source /tmp/jsonx-throughput-baseline/src \
  --source /tmp/jsonx-throughput-final-bounded/src --rounds 5 --repetitions 3 \
  --output /tmp/jsonx-throughput.json
```

No existing JSONX benchmark floors or latency ceilings were changed.
