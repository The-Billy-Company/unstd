# JSON-array streaming without the throughput penalty

The optimized `jsonx.iterload` passes the unchanged event-export workloads with
lower median wall time than the current eager loader at all three sizes. On
300,000 records, complete consumption takes **216.76 ms**, versus **234.83 ms**
for `jsonx.load` and **1,119.04 ms** for the frozen original streaming reader.
Peak process RSS remains **39.16 MiB**, versus **415.80 MiB** eager: **90.58%
less memory**. The first record still requires only a 64 KiB source read.

The [final raw evidence](json-array-stream-speedup-results.json) retains every
sample and source/native-binary receipt. All **72 fresh worker processes**
matched the independently expected output and complete source digests. The
strict speed and memory gates all pass, with an empty failure list.

## Implementation

Default decoding processes bounded batches of complete records. The native
framer finds delimiters while skipping quoted string spans, and a guarded native
decoder constructs each synthetic array in one allocation before invoking the
existing exact decoder. A successful array parse can also prove an object-record
prefix directly, avoiding a separate full-window framing scan.

UTF-8 binary reads stay as bytes through this path, avoiding a byte-to-Unicode
copy for every window. A short incomplete suffix can join one next window;
large records use the existing linear fragment walker. Decoder options, custom
decoders, refusals and malformed windows retain the per-item fallback. General
parser loading is deferred until that fallback is needed. Failed speculation
does not replay user callbacks or repeatedly parse a shrinking invalid suffix.

## Same fixtures and timed work

This follows the [original memory experiment](json-array-stream.md). The
[profile](../../profiles/json_array_stream.py) preserves its fixture formulas,
read wrapper, schema/type checks, aggregation, source hashing and complete
decoded-field checksum. Their ASTs match the saved original harness exactly:

| Unchanged code | SHA256 of AST without location attributes |
| --- | --- |
| Fixture generator `_prepare` | `b3bfc12c81c64067420b77164531856b7717dc6d3d1f078d45b056b2b6b483c6` |
| `_CountingReader` | `76831e695de6f6fb1ff583da4948376b196fededeaa5f727af0d5087a90ce33c` |
| Complete timed read/decode/hash/aggregate block | `f5a6b3a04bbd86045a95d1ebca8df50f4d0a47da4ba384e685e76173396b48e2` |

The fixture bytes and expected aggregates also match the
[original retained results](json-array-stream-results.json) exactly:

| Records | Input bytes | Source SHA256 | Decoded-fields SHA256 |
| ---: | ---: | --- | --- |
| 10,000 | 2,823,953 | `24a18ec0eb64cc643519ce923f4bd0cdfbb97762d9a7d559e2dd7ea26346c922` | `3d59a078c7d3096ce95520e7087381a3151fcda7d6de07e937c293afd5e3b5be` |
| 100,000 | 28,339,600 | `f4f921b387e1dcd94afa87e4fbdd4b5436752f2ab4b649db0f481215fd07c3d3` | `4d2fc6e4cc3a489ced81f87a4aaf337c08da365577a3c8138b82600edb0e4834` |
| 300,000 | 85,241,075 | `f8cd3cab8a5bb8ff11062146992db4da01a9c2ef7188f62c43dfaa21fbe2e279` | `3e49ee13d6c880244eef776a9496a38471b7bacfe33676c616f229bcd0c0a1a5` |

Each worker consumes the complete input and verifies every decoded field against
formula-derived expectations. Fixture generation and provenance checks occur
outside the timer. Source reading, hashing, decoding, schema/type validation and
aggregation remain inside the same timed block. No records or input bytes were
removed, and the 65,536-byte read budget remains unchanged.

## Three fresh-process controls

Each fixture has seven rounds containing these three lanes:

- Current eager `jsonx.load`, using the final candidate source and native binary.
- Optimized `jsonx.iterload`, using that same candidate source and native binary.
- Original `jsonx.iterload`, using an isolated frozen source tree and its original
  native binary.

Lane order reverses between rounds, so both relevant comparisons reverse their
relative order. Every lane runs in a fresh interpreter. GC remains enabled.
There are 63 ordinary workers and nine additional allocation-tracing workers;
the traced workers do not contribute to the ordinary timing or RSS medians.

An independent eager-decoder improvement entered this shared checkout during
development. It was preserved: the eager control below uses the final, improved
decoder. This comparison therefore measures streaming against the current
eager implementation. The old streaming lane remains frozen separately.

## Complete-consumption performance

These are separate medians of the seven ordinary processes per lane.

| Records | Input MiB | Original stream wall ms | Current eager wall ms | Optimized stream wall ms | Stream/eager ratio of medians |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10,000 | 2.69 | 37.726 | 7.465 | 7.200 | 0.9644× |
| 100,000 | 27.03 | 363.446 | 76.197 | 71.139 | 0.9336× |
| 300,000 | 81.29 | 1,119.039 | 234.835 | 216.757 | 0.9230× |

The speed gate uses the median of the seven **matched streaming/eager wall
ratios**, with a strict maximum of **1.0** at every fixture. It does not use the
ratio of separately calculated medians. Both estimators are retained:

| Records | Median paired stream/eager wall ratio | Median paired old/optimized stream speedup | Current eager CPU ms | Optimized stream CPU ms |
| ---: | ---: | ---: | ---: | ---: |
| 10,000 | 0.9584× | 5.1839× | 7.461 | 7.201 |
| 100,000 | 0.9268× | 5.0618× | 76.171 | 71.011 |
| 300,000 | 0.9260× | 5.1626× | 234.407 | 216.697 |

All three fixtures pass. At 10,000 rows, five of seven matched pairs were
faster; the other two were slower by 0.09% and 0.29%. All matched pairs at the
two larger sizes were faster. The evidence supports these median workload
results, rather than a claim that every individual run is faster.

## Memory and first record

RSS is the complete process high-water mark, including interpreter and backend
imports. It is not baseline-subtracted. macOS `resource.ru_maxrss` is recorded
in bytes; the profile normalizes Linux's KiB value separately.

| Records | Eager peak RSS MiB | Optimized peak RSS MiB | Eager/stream RSS | Eager bytes before first item | Streaming bytes before first item | Eager first item ms | Streaming first item ms |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10,000 | 51.39 | 39.03 | 1.32× | 2,823,953 | 65,536 | 3.447 | 0.104 |
| 100,000 | 164.17 | 39.14 | 4.19× | 28,339,600 | 65,536 | 35.197 | 0.102 |
| 300,000 | 415.80 | 39.16 | 10.62× | 85,241,075 | 65,536 | 108.454 | 0.109 |

Streaming peak RSS grew only **131,072 bytes (0.125 MiB)** across 30× more
records. The largest-fixture ratio exceeds the requested **3×** reduction, and
growth stays below the requested **16 MiB** bound. Every streaming read request
is at most 65,536 bytes and every worker consumes the entire source.

Separate traced workers recorded streaming peaks of 1,185,097, 1,185,099 and
1,185,099 bytes. The original per-item stream used roughly 0.29 MB in these
traced runs; batching uses a larger constant window while preserving the RSS
savings. Eager traced peaks were 42,212,864, 423,461,337 and 1,273,277,532 bytes.
Traced allocations and physical resident memory are different quantities.

## Correctness, provenance and reproduction

The final buffer-handling fix and native stream helpers pass **1,119 public and
native tests**, including tiny/short reads, UTF-8/16/32 boundaries, BOM handling,
raw surrogates, malformed input, buffer subclasses, custom decoder behavior,
gzip streams, caller ownership and bounded-memory controls. Changed Python
sources and this benchmark pass Ruff, formatting and strict mypy.

The complete serialization suite passes **4,517 tests**, with 24 capability
skips and five opt-in cases deselected. All five serialization public-API
contracts pass. The [framing diagnostics](json-array-framing-speedup-results.json)
repeat 471,365 independently checked documents with zero mismatches. The
existing native JSON acceleration qualification reports no errors. Working
reader/kernel sources and the installed native binary match the final measured
candidate exactly.

```console
.venv/bin/pytest tests/suites/product/serde/unit/test_jsonx_stream.py tests/suites/product/serde/unit/test_jsonx_stream_native.py -q
```

Captured October 27, 2026 at 21:44:59 UTC on macOS 26.6 arm64, CPython 3.14.9,
orjson 3.12.0, msgspec 0.22.0 and NumPy 2.5.3. The profile rejects product imports
outside each selected source tree, records the actual loaded files, and checks
both source/native manifests before and after measurement. All those checks
pass for the final immutable snapshots.

| Artifact | SHA256 |
| --- | --- |
| Profile harness | `42020de5f219b06f25da79c633d7b6ffac89e3fb484633e48b7854c3e2ce0d2d` |
| Final `jsonx.py` | `ac8f97d22c661afe3a7d2d630172e19999e870cae74a01866e8bc57074e66f0e` |
| Final `_stream.py` | `59a381247d9e63a92646377a7d8a77002d8588fe7c829e2e6d7bcdb1133026b8` |
| Final native binary | `33640d5aa72178aada70a6c1a9d17380194c52c57452979d896bc96f27debbe6` |
| Original native binary | `8261c26070830e1cf796216662f7a40ef9d0157c833db20c0ccf33cc92c6d07f` |

From the repository root, using the retained source snapshots and the same
interpreter/dependencies:

```console
.venv/bin/python bench/profiles/json_array_stream.py --source .local/json-array-stream-speed/candidate-final/src --compare-source .local/json-array-stream-speed/before/src --rows 10000,100000,300000 --rounds 7 --chunk-size 65536 --tracemalloc --max-stream-slowdown 1.0 --min-rss-reduction 3 --max-stream-rss-growth-mib 16 --output /tmp/json-array-stream-speedup-results.json
```

For a standalone source reproduction, the
[source archive](json-array-stream-sources.tar.gz) contains both versions' JSON
sources, their native headers, a file-hash manifest, licenses, and `build.py`.
It contains no compiled binaries. Its SHA256 is
`4b10dd7c6b2b10b4d5568e81bc2108d1069789921b9c0582c2024b69f07bda6b`.
Both kernels were rebuilt from the extracted archive and passed a three-lane
1,000-record smoke comparison with matching source/field digests. The measured
candidate's [actual compiler/linker arguments](json-array-stream-build.json)
are retained separately.

```console
mkdir -p /tmp/unstd-array-proof
tar -xzf bench/reports/serde/json-array-stream-sources.tar.gz -C /tmp/unstd-array-proof
.venv/bin/python /tmp/unstd-array-proof/json-array-stream-sources/build.py
.venv/bin/python bench/profiles/json_array_stream.py --source /tmp/unstd-array-proof/json-array-stream-sources/after/src --compare-source /tmp/unstd-array-proof/json-array-stream-sources/before/src --rows 10000,100000,300000 --rounds 7 --chunk-size 65536 --tracemalloc --max-stream-slowdown 1.0 --min-rss-reduction 3 --max-stream-rss-growth-mib 16 --output /tmp/json-array-stream-reproduced.json
```

The earlier memory report and raw results remain unchanged. Failed development
attempts, including source-drift rejection and residual speed-gate failures,
were retained in `.local/json-array-stream-speed/initial-speedup-attempt.json`
and `stable-first-full-attempt.json`. The first passing bytes-path capture,
before the final buffer-subclass fix, remains at
`.local/first-passing-bytes-report.json`. No threshold or frozen speed ceiling
was relaxed to produce the final result.

These are synthetic default-decoding UTF-8 binary workloads on one host with an
uncontrolled OS file cache. They establish the measured throughput and memory
results for these fixtures. Other shapes, encoding modes, custom options and
hosts have their own costs. Memory still depends on chunk size and the largest
individual item, plus values retained by callers or hooks.
