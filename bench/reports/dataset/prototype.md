# Historical SQLite prototype measurements

`unstd.dataset` provides reusable indexed snapshots for JSONL workloads that
need row selection and a known count. The standalone driver compares eager
parsing, streaming reads, and indexed access using deterministic synthetic
nested records. It needs no application checkout or external data.

## Run

```sh
python -m bench.profiles.dataset --output bench/reports/dataset/measured.json
```

The default uses 25,000 records, a 32-row prefix, 64 fixed random indices, and
three timing samples per operation. Every operation verifies its output against
independent expected values. Build cost, storage, latency, and memory are
reported separately; slower indexed cases remain visible.

## Historical capture

The measurements below describe the previous implementation and fixture
revision. Their original timings and hashes remain in [measured.json](prototype-measured.json).
Publication metadata records removal of unrelated source receipts and replacement
of machine-specific paths with public origin labels. These numbers do not claim
performance for the current API revision; rerun the driver for current results.

On the recorded Apple arm64 host with Python 3.14.9, indexed selection of
64 rows took **0.425 ms**, versus **26.199 ms** for a fair single streaming
scan: **61.7× faster**, decoding **64 rows instead of 24,949**. Every selected
row matched independently generated expected values, including nested messages,
tool schemas and tool-call arguments.

| Operation | Median time | Peak traced Python allocations | Rows decoded |
| --- | ---: | ---: | ---: |
| Build artifact from JSONL | 122.978 ms | 2.39 MiB | 25,000 |
| Eager stdlib parse, prefix 32 | 119.001 ms | 135.12 MiB | 25,000 |
| Streaming NDJSON, prefix 32 | 0.106 ms | 250.17 KiB | 32 |
| Dataset, prefix 32 | 0.263 ms | 124.85 KiB | 32 |
| Eager stdlib parse, random 64 | 111.748 ms | 135.12 MiB | 25,000 |
| Streaming NDJSON scan, random 64 | 26.199 ms | 378.51 KiB | 24,949 |
| Dataset, random 64 | 0.425 ms | 248.32 KiB | 64 |
| Eager stdlib parse, full aggregate | 119.198 ms | 135.12 MiB | 25,000 |
| Retain lines, stdlib parse, full aggregate | 69.859 ms | 46.10 MiB | 25,000 |
| Streaming NDJSON, full aggregate | 30.300 ms | 140.58 KiB | 25,000 |
| Dataset, full aggregate | 36.488 ms | 15.01 KiB | 25,000 |

The artifact used **25,673,728 bytes** for **23,561,513 bytes** of JSONL:
**9.0% storage overhead**. Its measured build cost amortized after **five**
random-selection reads versus streaming scans, or **two** reads versus eager
full-object parsing. Those counts include the measured artifact build cost and
assume the same artifact is reused.

## Losses and limits

A single streaming prefix was **2.49× faster** than opening and validating a
Dataset. Full sequential NDJSON aggregation was **1.20× faster** too. Use the
existing streaming reader for those one-pass workloads; indexing earns its cost
when records are revisited or selected out of order. Full Dataset aggregation
was **1.91× faster** than the retained-lines/stdlib pattern and used
far fewer traced allocations.

Each operation runs in a fresh process. Timing includes opening and closing
files/connections and Dataset schema validation. Fixture generation, imports,
process startup, independent expected-value comparison and output hashing stay
outside the timer. Tracemalloc and actual decoder instrumentation run in separate
passes and must produce the same expected outputs. Full scans verify arithmetic
aggregates; selected reads compare complete rows.

Tracemalloc does not measure native SQLite allocations or process RSS. The JSON
also records process lifetime peak RSS after timing and before tracemalloc:
about **58.2 MiB** for indexed random reads, **56.4 MiB** for streaming random
reads and **246.3 MiB** for eager random reads. These totals include imports and
worker setup. The OS filesystem cache is not flushed; build measurements include
JSONL decoding, row encoding, SQLite commit, finalized-file copying, file fsync
and atomic replacement. This is one host and one synthetic shape, without a
claim about live service latency or universal speedups.

The report pins imported product/helper paths and SHA256 hashes, including the
loaded JSON, orjson and msgspec native extensions. It records orjson **3.12.0**,
msgspec **0.22.0** and NumPy **2.5.3**. Parent and worker processes check those
files and backend versions before and after execution; drift refuses evidence
publication. Origins use repository-relative, distribution-qualified, or runtime
labels instead of machine-specific directory paths.

## Correctness and packaging

The previous revision passed 89 sequence, corruption, cleanup, permission, and
publication tests. Its eight operation workloads retain seven-round
[timing samples](operations.json). These reports are historical until recaptured
for the current implementation.

The historical [wheel smoke receipt](packaging.json) verifies that the captured
code and guide shipped in a portable wheel. An isolated Python process with site packages
disabled builds 1,000 rows and checks length, indexed access, and slicing from
that wheel. Optional native build hooks were disabled for this base-install
check; the new package requires none of them.
