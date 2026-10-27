# Generator-based JSON-array exports

`jsonx.dump_array(records, destination)` writes an iterable as one compact JSON
array, encoding each record before requesting the next and batching output into
bounded writes. The existing `dump` function retains its stdlib behavior.

For the retained 300,000-record, **112.13 MiB** export, the new API took
**0.780 seconds** and **39.50 MiB peak process RSS**, versus **2.139 seconds**
and **512.52 MiB** for `list(generator)` followed by the existing compact
`jsonx.dump`. That is **2.74× faster and 92.3% less peak RAM** for this complete
generated-export pipeline. The 3× minimum RSS-reduction gate passed at 12.98×.

The [raw evidence](json-array-write-results.json) contains all 48 ordinary
timing/RSS processes and 16 separate diagnostic processes. Every process
generated and wrote the requested record count, and its complete output length
and SHA256 matched an independent stdlib oracle. No failures were reported.

## Increasing corpus size

Medians of three fresh processes per API and corpus; record generation, list
construction where required, encoding, real buffered file writes and flushes
are inside each timer. Imports, output opening, source receipts and subsequent
output verification are outside it. Garbage collection remains enabled.

| Records | Output MiB | Existing `dump` RSS MiB | `dump_array` RSS MiB | Existing `dump` seconds | `dump_array` seconds |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.37 | 40.70 | 39.31 | 0.00641 | 0.00287 |
| 10,000 | 3.72 | 54.81 | 39.19 | 0.05680 | 0.02460 |
| 100,000 | 37.31 | 196.22 | 39.17 | 0.68696 | 0.21254 |
| 300,000 | 112.13 | 512.52 | 39.50 | 2.13858 | 0.78039 |

Streaming RSS stayed within 0.34 MiB across a 300× increase in records. RSS is
the complete process high-water mark, including interpreter and backend imports,
without baseline subtraction. macOS reports this value in bytes.

The largest corpus also retains alternatives, including bulk byte encoding:

| Export pipeline | Median seconds | Peak RSS MiB |
| --- | ---: | ---: |
| Generator → `dump_array` → binary file | 0.780 | 39.50 |
| List → `jsonx.dump` → UTF-8 text file | 2.139 | 512.52 |
| List → stdlib `json.dump` → UTF-8 text file | 2.102 | 512.25 |
| List → `jsonx.dumpb` → binary file | 0.998 | 1,056.17 |

All eager lanes include creating their required list from the same generator.
These results concern generated exports, rather than serialization time for
an already resident list. Bulk `dumpb` remains faster on the 1,000-record
fixture (1.92 ms versus 2.87 ms); streaming is useful when bounded memory and
incremental output matter. Binary and text output files contain identical bytes.

## Independent output and incremental delivery

The [profile](../../profiles/json_array_write.py) creates independently allocated
event records with IDs, Unicode response text, nested metrics including finite
floats, and tags. The parent constructs the expected complete-byte digest using
stdlib's compact, unescaped per-record encoder and literal array framing. It
does not call the new writer to derive an expected answer or retain a complete
array. Each child hashes its finished output after the timer and RSS sample.

Primary timing processes use actual file handles without counting wrappers.
Separate diagnostic processes measure Python `write` calls and delivery to the
underlying buffered stream. Their elapsed times do not enter speed ratios.

For 300,000 records, the existing `dump` issued **9,600,001 Python write calls**,
while `dump_array` issued **1,796**, with every request at most 65,536 bytes.
These are API calls, not kernel system calls. The streaming writer's first
complete record reached the buffered sink after consuming **169 records**;
the eager APIs consumed all **300,000** first. The immediate opening bracket
does not count as a complete record. No durability or `fsync` claim is made.

## Provenance and reproduction

Recorded October 27, 2026 at 21:13 UTC on macOS 26.6 arm64, CPython 3.14.9,
orjson 3.12.0, msgspec 0.22.0 and NumPy 2.5.3. Samples run serially with rotated
and reversed API order. The OS file cache and other host activity are not
controlled; these synthetic single-host measurements are not universal bounds.

The run selected a private immutable copy of `src/unstd` to preserve the
experiment while other work continued in the shared checkout. The evidence
records the selected path and hashes of the driver and every serde source and
native binary. All captured files matched the checkout after measurement.
Subsequent concurrent read-framing work changed `_stream.py`, which these
export pipelines do not import or use. The writer, codec, recursion helpers,
native binary and driver still matched their captured hashes at final review.
The existing `dump` function's source also matched its pre-change snapshot.
The measured shared-workspace snapshot includes independent array-reader work;
the writer commit excludes those reader changes. Their captured receipts are
historical provenance, and the writer has no runtime dependency on them.

From the repository root with the same dependencies, run:

```console
.venv/bin/python bench/profiles/json_array_write.py --rows 1000,10000,100000,300000 --rounds 3 --min-rss-reduction 3 --output bench/reports/serde/json-array-write-results.json
```

Use `--source /path/to/snapshot/src` to select an isolated source copy. Every
worker checks import provenance and source stability, and the parent checks
the complete manifest again before saving results. The report is written before
an unmet RSS gate exits nonzero. Product edits require new measurements.

## Correctness and integration

The new writer suite passes **68 tests**, including independent stdlib bytes,
generated values, backend fallback, one-shot and reused mutable-row producers,
tiny chunks, oversized Unicode records, options and callbacks, text/binary
gzip streams, short writes, blocked sinks and invalid counts. It also checks
bounded traced allocations as record counts increase and runs in a fresh
process with optional backends and the native extension absent.

The full serialization run passed **4,151 tests**, with 24 capability skips and
five opt-in cases deselected. Serialization public API contracts, the new
operation's older-revision workload handling, Ruff, formatting, strict mypy and
`git diff --check` passed.

```console
.venv/bin/pytest tests/suites/product/serde -q --require-extra serde
.venv/bin/pytest tests/suites/assurance/gates/packaging/test_public_api.py -q -k 'unstd.serde'
.venv/bin/pytest tests/suites/assurance/gates/bench/test_speed_coverage.py -q -k dump_array
```

The [32-record operation capture](json-array-write-operation.json) adds a
baseline for the new public API without changing existing operation ceilings.
It is a new-operation latency baseline, separate from the export comparison.
The new codec workload fingerprint and report receipt were updated explicitly.
A separate repository-wide frozen-workload check reported a missing
`speed_cases_math.py` fingerprint from concurrent work in this shared checkout;
the writer's workload, baseline row and captured receipt pass focused checks.

The API buffers one chunk plus the largest record and any values retained by
the producer or callbacks. It cannot bound a producer's own allocations or a
single arbitrarily large record. Errors leave partial output, batching may
consume records ahead of a sink failure, and the caller owns flushing, closing
and atomic publication. Encoding options and recursion limits apply per record.
