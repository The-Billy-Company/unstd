# Initial mapped-format diagnostic

This historical capture exposed a fresh-open cost on macOS before the native
read-only mapping owner was added. CPython's standard mapping constructor calls
`F_FULLFSYNC` for file-backed mappings on macOS, including read-only access;
CPython tracks that legacy workaround in [issue113861](https://github.com/python/cpython/issues/113861).
The product and helper hashes identify the exact captured revision. These
numbers are diagnostic history, not current-implementation performance claims.

`unstd.dataset` stores newline-terminated JSON records followed by a compact
64-bit offset table. The mapped snapshot provides constant-time length, indexed
selection, lazy row traversal, and explicit bounded batches. This standalone
benchmark compares those operations with eager and streaming JSONL readers.

## Reproduce

```sh
python -m bench.profiles.dataset --output bench/reports/dataset/measured.json
```

The default generates 25,000 deterministic nested JSON records. It selects a
32-row prefix and 64 fixed random positions, then computes complete-corpus
aggregates. Every operation runs in a fresh process with three timing samples.
Fixture generation, imports, process startup, and output comparisons are outside
the timers. File opening, mapped-format admission, row selection, decoding, and
closing are included. Selected rows must match complete independently generated
values; full scans must match arithmetic aggregates.

The [diagnostic raw report](mapped-initial-measured.json) pins public origin labels, imported
Python/native file hashes, and backend versions. Parent and worker processes
check them before and after execution. Source drift prevents publication.

## Recorded result

On the captured Apple arm64 host with Python 3.14.9, selecting 64 scattered rows
with `take` took **8.437 ms**, versus **29.890 ms**
for a single streaming scan: **3.54× faster**.
It decoded 64 rows instead of 24,949. Opening and closing a fresh mapping is
included, so this describes a complete read operation rather than a reused
handle's lookup latency.

| Operation | Median time | Peak traced Python allocations | Rows decoded |
| --- | ---: | ---: | ---: |
| Build indexed snapshot from JSONL | 90.340 ms | 1.58 MiB | 25,000 |
| Eager stdlib, prefix 32 | 127.670 ms | 135.12 MiB | 25,000 |
| Streaming NDJSON, prefix 32 | 0.144 ms | 250.17 KiB | 32 |
| Dataset slice, prefix 32 | 4.581 ms | 151.51 KiB | 32 |
| Eager stdlib, random 64 | 130.395 ms | 135.12 MiB | 25,000 |
| Streaming scan, random 64 | 29.890 ms | 378.51 KiB | 24,949 |
| Dataset.take, random 64 | 8.437 ms | 300.94 KiB | 64 |
| Eager stdlib, full aggregate | 146.522 ms | 135.12 MiB | 25,000 |
| Retain lines, stdlib full aggregate | 94.569 ms | 46.10 MiB | 25,000 |
| Streaming NDJSON, full aggregate | 35.751 ms | 140.59 KiB | 25,000 |
| Dataset.iter_rows, full aggregate | 44.540 ms | 131.21 KiB | 25,000 |
| Dataset.batches(1024), full aggregate | 70.098 ms | 8.29 MiB | 25,000 |

Building the snapshot took **90.340 ms**. Its **23,761,577 bytes**
compare with **23,561,513 bytes** of source JSONL:
**0.849% storage overhead** for this fixture.
The extra 200,064 bytes are the row-offset table and fixed header. Build cost is
recovered after **5** such random-selection reads versus streaming,
assuming the same artifact is reused.

## Losses and limits

Fresh mapped access is substantially slower than streaming for a single short
prefix: **31.75×** in this capture. A complete row-by-row
aggregate is **1.25× slower** than streaming too.
Bounded 1,024-row batches used **8.29 MiB** of traced Python allocations
and were **1.96× slower** than streaming for this nested-record aggregate.
Batching gives callers an explicit materialization boundary; it does not provide
a universal throughput improvement. Use the streaming reader for one-pass
workloads when an index or known total is unnecessary.

The source layout, offset checking, decoding, and mapping lifecycle all contribute
to latency. The separate [access qualification](access.md) compares the native
and portable paths on the same data and isolates reused-handle operations. Do
not treat an end-to-end comparison with a streaming scan as the native kernel's
speedup.

Tracemalloc excludes native allocations and mapped pages. The JSON also records
process lifetime peak RSS after timing and before the tracing pass:
**57.72 MiB** for indexed random selection,
**56.50 MiB** for streaming random selection, and
**246.67 MiB** for eager selection. These totals include imports and
worker setup. The OS filesystem cache is not flushed. This is one host and one
synthetic shape, with no claim about service latency or universal speedups.

Build measurements include source decoding, row encoding, offset spooling,
SHA256 content/index checksumming, file synchronization, and atomic replacement.
Traced allocations and decode instrumentation run separately from wall timing.
Single-row paths observe decoder calls; bounded-batch instrumentation counts
rows returned by each observed batch decoder call. Every pass rechecks output.

## Correctness, native access, and historical evidence

The package's product suites cover Python sequence behavior, ordered duplicate
selection, lazy slices, bounded batches, corruption checks, publication failure,
resource cleanup, and portable/native parity. Run them with:

```sh
python -m pytest tests/suites/product/dataset -q
```

The current implementation separates sequence behavior in `reader.py`, format
and lifecycle mechanics in `storage.py`, and optional checked offset/copy loops
in the private native extension. JSON decoding stays with the existing serde
APIs. The native kernel is optional; the public API has a portable path.

The earlier SQLite prototype remains in [prototype.md](prototype.md) and
[prototype-measured.json](prototype-measured.json). Its original operation
capture is [operations.json](operations.json). Those artifacts are historical,
with metadata normalization recorded explicitly, and are not current-API speed
claims. Current operation evidence and the [wheel receipt](packaging.json) are
retained separately when recaptured for this revision.
