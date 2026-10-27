# Indexed JSON dataset measurements

`unstd.dataset` stores newline-terminated JSON records followed by a compact
64-bit offset table. The mapped snapshot provides constant-time length, indexed
selection, lazy traversal, and explicit bounded batches. This standalone proof
compares those operations with eager and streaming JSONL readers.

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

## Recorded result

On the captured Apple arm64 host with Python 3.14.9, selecting 64 scattered rows
with `take` took **0.377 ms**, versus **26.142 ms** for a single
streaming scan: **69.29× faster**, decoding **64 rows instead of 24,949**.
Opening and closing a fresh mapping is included; this is a complete read
operation rather than a reused handle's lookup latency.

| Operation | Median time | Peak traced Python allocations | Rows decoded |
| --- | ---: | ---: | ---: |
| Build indexed snapshot from JSONL | 72.937 ms | 1.58 MiB | 25,000 |
| Eager stdlib, prefix 32 | 107.370 ms | 135.12 MiB | 25,000 |
| Streaming NDJSON, prefix 32 | 0.124 ms | 250.17 KiB | 32 |
| Dataset slice, prefix 32 | 0.293 ms | 151.46 KiB | 32 |
| Eager stdlib, random 64 | 107.767 ms | 135.12 MiB | 25,000 |
| Streaming scan, random 64 | 26.142 ms | 378.51 KiB | 24,949 |
| Dataset.take, random 64 | 0.377 ms | 300.89 KiB | 64 |
| Eager stdlib, full aggregate | 113.775 ms | 135.12 MiB | 25,000 |
| Retain lines, stdlib full aggregate | 68.266 ms | 46.10 MiB | 25,000 |
| Streaming NDJSON, full aggregate | 28.873 ms | 140.59 KiB | 25,000 |
| Dataset.iter_rows, full aggregate | 31.556 ms | 131.21 KiB | 25,000 |
| Dataset.batches(1024), full aggregate | 39.221 ms | 4.61 MiB | 25,000 |

Building the snapshot took **72.937 ms**. Its **23,761,577 bytes** compare
with **23,561,513 bytes** of source JSONL: **0.849% storage overhead** for
this fixture. The extra 200,064 bytes are the offset table and fixed header.
Build cost is recovered after **3** such random-selection reads versus
streaming, assuming the same artifact is reused.

## Native access and the mapping lifetime

The captured dataset operations used kernel version **2**. Every dataset worker
separately observed its actual owner as
`unstd.dataset._dataset_native.Mapping`; a loaded
extension alone is not treated as proof of native mapping use. The separate
[access qualification](access.md) compares the same implementation with native
execution disabled, observes native entrypoints, and measures mapping setup
separately from reused-handle reads. Its fixture is smaller and intentionally
isolates different costs; do not mix its timings with this table.

On POSIX, a private native owner maps the already-published file read-only.
That avoids a redundant synchronization request made by CPython's standard
mapping constructor on macOS, tracked in [CPython issue 113861](https://github.com/python/cpython/issues/113861).
The [initial diagnostic](mapped-initial.md) and
[raw diagnostic capture](mapped-initial-measured.json) retain the slower result
that exposed this cost. Windows and compiler-free installations use stdlib
mapping ownership with its platform behavior. Reuse an open dataset for repeated
reads so setup is paid once.

## Losses and limits

A single streaming prefix was **2.36× faster** than fresh indexed access in this
capture. Streaming full aggregation was **1.09× faster** than lazy dataset iteration.
Bounded 1,024-row batches used **4.61 MiB** of traced Python allocations and were
**1.36× slower** than streaming on this nested-record aggregate. Batching gives
callers an explicit materialization boundary; it does not promise a throughput
improvement on every shape. Use the streaming reader for one-pass workloads
when an index or known total is unnecessary.

Tracemalloc excludes native allocations and mapped pages. The raw JSON also
records process lifetime peak RSS after timing and before the tracing pass:
**57.64 MiB** for indexed random selection,
**56.97 MiB** for streaming random selection, and
**246.25 MiB** for eager selection. These totals include imports and worker setup.
The OS filesystem cache is not flushed. This is one host and one synthetic
shape, with no claim about service latency or universal speedups.

Build measurements include source decoding, row encoding, offset spooling,
SHA256 content/index checksumming, file synchronization, and atomic replacement.
Traced allocations and decode instrumentation run separately from wall timing.
Single-row paths observe decoder calls; bounded-batch instrumentation counts
rows returned by each observed batch decoder call. Every pass rechecks output.

## Code identity and correctness

The measured package was loaded from an isolated installation of the built
wheel with SHA256
`94e710a726b7447f68af10f5d122d95f0f33df5cbb843b77abf45f93611873c9`.
The import boundary was asserted before capture. The [raw report](measured.json)
pins public origin labels, imported Python/native file hashes, and backend
versions; parent and worker processes check them before and after execution.
Source drift prevents publication. The recorded optional JSON backends are
orjson **3.12.0** and msgspec **0.22.0**; the base package works without them,
but these measurements describe the recorded backend configuration.

Product suites cover sequence behavior, ordered duplicate selection, lazy
slices, bounded batches, corruption checks, publication failure, resource
ownership, and portable/native parity:

```sh
python -m pytest tests/suites/product/dataset -q
```

Sequence behavior lives in `reader.py`; the file format, builder, and mapping
lifecycle live in `storage.py`. The optional native extension owns read-only
mapping resources and checked offset/copy loops. JSON parsing stays with the
existing serde APIs. The [wheel receipt](packaging.json) records separate
base-install verification; [operation evidence](operations.json) retains
current registered-workload measurements.

The earlier SQLite prototype remains in [prototype.md](prototype.md) and
[prototype-measured.json](prototype-measured.json), with its original operation
capture in [prototype-operations.json](prototype-operations.json). These and the initial mapping
diagnostic are explicitly historical. Their source hashes and numerical results
are retained; publication metadata records normalized labels and origins.

The [artifact manifest](artifact.json) binds the built wheel, package files,
raw proof reports, installed-consumer smoke check, and final 411 passing tests.
