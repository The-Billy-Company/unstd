# Incremental JSON-array consumption

`jsonx.iterload` lets a consumer process a large existing JSON array without
retaining the source or the complete decoded list. In the retained macOS run,
consuming 300,000 event records from an 81.3 MiB array used **39.34 MiB peak
process RSS**, compared with **417.59 MiB** for `jsonx.load`: a **90.6% reduction**
and a 10.61× ratio. Streaming delivered the first record after reading 64 KiB.
Complete consumption took longer: 1.483 seconds streamed versus 0.353 seconds
with the eager loader.

The [raw evidence](json-array-stream-results.json) retains all subprocess
samples, independent expected results, complete source and decoded-field
digests, read counts, first-result measurements, backend versions and source
receipts. Both requested memory gates passed, and the failure list is empty.

## Same work and independent answers

The [profile](../../profiles/json_array_stream.py) writes deterministic ASCII
JSON arrays in the parent process. Each record contains an integer ID, token
count, boolean outcome, category and 208-character response. Expected sums,
category counts, passed counts and a SHA256 checksum over every field come from
the generator formulas. They do not come from either JSON decoder.

Every measured worker reads one array through the public API, consumes every
record, verifies the record schema and field types, and computes that same
summary. A raw, unbuffered source wrapper also hashes every delivered source
byte. The parent checks the complete source size and SHA256 as well as all
expected decoded results. All **24 distinct worker processes** passed these
checks: 18 ordinary runs and six separate allocation-tracing runs. No fixture
generation occurs in a worker.

The eager control calls the existing `jsonx.load` and iterates its returned
list. The streaming lane directly iterates `jsonx.iterload` with
`chunk_size=65536`. The consumer builds only an aggregate in either lane;
the eager API retains its complete list. Their common aggregation and hashing
costs remain inside the timer.

## Ordinary process measurements

These are medians of three fresh processes per API and fixture. Execution order
alternates within each paired round. GC remains enabled. RSS is the complete
process high-water mark, including interpreter and backend imports; it is not
baseline-subtracted. The macOS `resource.ru_maxrss` value is already in bytes.

| Records | Input MiB | Eager peak RSS MiB | Streaming peak RSS MiB | Eager / streaming RSS | Eager wall seconds | Streaming wall seconds |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10,000 | 2.69 | 51.45 | 38.73 | 1.33× | 0.00865 | 0.04050 |
| 100,000 | 27.03 | 164.52 | 39.41 | 4.17× | 0.11673 | 0.49049 |
| 300,000 | 81.29 | 417.59 | 39.34 | 10.61× | 0.35341 | 1.48292 |

The median streaming peak grew **638,976 bytes (0.61 MiB)** from 10,000 to
300,000 rows while the input grew about 30×. The largest-fixture RSS ratio
exceeded the requested 3× threshold, and the growth stayed below the requested
16 MiB bound.

The median paired eager/streaming wall ratios were 0.214×, 0.223× and 0.238×.
This is a memory and first-result improvement with a throughput cost: the
streaming path was approximately 4.2–4.7× slower to consume these complete
fixtures. Median eager/streaming CPU times were respectively 0.00845/0.03940,
0.11182/0.46899 and 0.33514/1.38469 seconds. At 300,000 rows the complete
read/decode/hash/aggregate workloads processed 230.0 MiB/s eager and 54.8 MiB/s
streamed. These are workload measurements, not isolated decoder throughput.

## First record and complete-source checks

| Records | Eager bytes before first record | Streaming bytes before first record | Eager first record ms | Streaming first record ms |
| ---: | ---: | ---: | ---: | ---: |
| 10,000 | 2,823,953 | 65,536 | 3.679 | 0.246 |
| 100,000 | 28,339,600 | 65,536 | 65.299 | 0.334 |
| 300,000 | 85,241,075 | 65,536 | 200.552 | 0.332 |

Every streaming read request was at most 65,536 bytes; none requested an
unbounded read. Each worker consumed the entire source despite yielding its
first record early. The byte counts describe the source API and do not measure
physical disk traffic.

| Records | Complete source SHA256 | Decoded fields SHA256 |
| ---: | --- | --- |
| 10,000 | `24a18ec0eb64cc643519ce923f4bd0cdfbb97762d9a7d559e2dd7ea26346c922` | `3d59a078c7d3096ce95520e7087381a3151fcda7d6de07e937c293afd5e3b5be` |
| 100,000 | `f4f921b387e1dcd94afa87e4fbdd4b5436752f2ab4b649db0f481215fd07c3d3` | `4d2fc6e4cc3a489ced81f87a4aaf337c08da365577a3c8138b82600edb0e4834` |
| 300,000 | `f8cd3cab8a5bb8ff11062146992db4da01a9c2ef7188f62c43dfaa21fbe2e279` | `3e49ee13d6c880244eef776a9496a38471b7bacfe33676c616f229bcd0c0a1a5` |

The largest fixture's independently expected summary includes 300,000 rows,
ID sum 44,999,850,000, token sum 1,228,169,520, 257,142 passed records, 75,000
records per category and 62,400,000 response characters.

The separate [framing diagnostics](json-array-framing-results.json), reproduced
by [this driver](../../diagnostics/json_array_framing.py), checked **471,365
documents with zero differential mismatches**: escaped strings across small
chunk boundaries, seeded valid/invalid inputs, and eight binary encodings.
Incrementally generated whitespace inputs from 10,000 to 10,000,000 bytes kept
peak traced memory near 2.6 KiB. Increasing one string item from 100,000 to
6,400,000 bytes showed approximately linear observed parsing time. These
diagnostics supplement the full-array memory experiment above.

## Separate allocation measurements

One additional fresh process per API and size enables `tracemalloc`. These
slower, instrumented processes are excluded from the ordinary timing and RSS
medians above. Their streaming peak traced allocations were 292,068, 296,461
and 294,694 bytes respectively, consistent with a small active parsing window
for these records. Eager peaks grew from 42,212,864 to 1,273,277,500 traced
bytes. Allocation tracing is supplemental: traced allocation peaks and resident
physical memory describe different quantities and must not be equated.

## Host, provenance and reproduction

Recorded October 27, 2026 at 20:55 UTC on macOS 26.6 arm64, CPython 3.14.9,
orjson 3.12.0, msgspec 0.22.0 and NumPy 2.5.3. Each worker records the files it
actually loaded. The complete serde source/binary manifest was identical
before and after the run.

The receipts identify the captured snapshot. Package-root documentation in
`src/unstd/__init__.py` changed subsequently; all serde implementation files,
the loaded native binary and the harness still matched their captured receipts
at final verification. The experiment does not claim that other concurrent
changes across the whole working tree were frozen.

Selected SHA256 receipts:

| Artifact | SHA256 |
| --- | --- |
| Profile harness | `033c4830e2e5f39a2b7936d94d67c402b6f5a4f6d6365b1792a5c13809189e7a` |
| `src/unstd/serde/_stream.py` | `ec559f7e241b26179b5af8c7d3c11e2982b59080bab45ae7ddb6acd49de05d2c` |
| `src/unstd/serde/jsonx.py` | `3c300cb8af8d9541fe620ef76c6512f3e5e651b62956e112a7e581587ed2f4cf` |
| Loaded `_json_native` binary | `8261c26070830e1cf796216662f7a40ef9d0157c833db20c0ccf33cc92c6d07f` |

From the repository root, with the same interpreter and dependencies:

```console
.venv/bin/python bench/profiles/json_array_stream.py --rows 10000,100000,300000 --rounds 3 --tracemalloc --min-rss-reduction 3 --max-stream-rss-growth-mib 16 --output bench/reports/serde/json-array-stream-results.json
```

The output is saved before a failed memory gate exits. Use `--source /path/to/src`
to select another source tree. `--rows` also accepts repeated arguments, and
`--chunk-size` controls the read budget. Temporary fixture files are removed
after consumption; their deterministic generation, lengths and hashes remain
in the evidence. Product source edits require a new run.

This single-host experiment uses synthetic records and an uncontrolled OS file
cache. It does not establish a universal latency bound or a fixed total memory
ceiling for arbitrary documents. A single large JSON item, decoder hooks, or
values retained by the consumer can increase memory; incremental parsing keeps
the active item and read chunks rather than the complete array.

## Correctness and integration checks

The new streaming suite passes **784 tests**, including accelerated and stdlib
decoding, one-byte reads, all supported binary encodings, escaped punctuation,
generated valid and malformed input, callbacks, gzip binary/text streams and
corrupt gzip trailers. Fresh subprocesses also run with orjson, msgspec, NumPy
and the native JSON extension blocked, proving the base-install path.

```console
.venv/bin/pytest tests/suites/product/serde/unit/test_jsonx_stream.py -q
.venv/bin/pytest tests/suites/product/serde -q --require-extra serde
.venv/bin/pytest tests/suites/assurance/gates/packaging/test_public_api.py -q -k 'unstd.serde'
.venv/bin/pytest tests/suites/assurance/gates/bench/test_speed_coverage.py -q -k iterload
```

The full serialization run before the final two base-install cases passed
3,974 tests, with 24 capability skips and five opt-in cases deselected. The
serialization API contracts and older-revision workload compatibility check
also passed. Changed Python sources, workloads and diagnostics pass Ruff,
formatting and strict mypy. The suite inventory and `git diff --check` pass.

The new 32-record latency workload has a separate
[capture](json-array-stream-operation.json) with its actual CPython 3.14
environment and source receipts. It was appended to the historical speed
inventory without changing any of its 278 existing case rows or ceilings. Its
latency is a new-operation baseline, not a before/after speedup claim.

Repository-wide API/benchmark gates still report unrelated mismatches in this
shared checkout: the filesystem `ensure_dir(check_first=...)` signature versus
its frozen manifest, and cache batch methods without registry workloads. These
do not affect the passing serialization contracts, but a clean whole-repository
gate is not claimed. Other work in the checkout was preserved.
