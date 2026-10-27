# Cache evidence

## Current typed-artifact and hot-path validation

The [native workflow report](hot-paths.md) and [sanitized raw timings](hot-paths.json)
record the typed cache in an actual private preprocessing caller. Complete warm
processing measured **1.688× on macOS arm64** and **1.560× on Linux arm64**;
Linux cold performance is neutral within host variation. The report retains
earlier cold regressions, exact-output checks, source hashes, installed-wheel
qualification, and controlled Linux syscall counts. Private caller source and
fixtures are owner-retained, so public package profiles cannot reproduce that
specific workflow.

Current scoped checks pass 144 cache tests and 77 native caller tests on each
measured platform; the current host check also passes 38 benchmark-coverage
tests. Linux runs native arm64 inside a
Docker Desktop Linux VM, with the qualifications in the [validation record](validation.md).
The [typed operation contracts](codec-operations.md) separately retain tiny-artifact
costs; they are not full-workflow speedup claims.

## Historical scale-upgrade measurements

The scale-upgrade capture added consistent batch reads, atomic batch writes, and
independent producers computing outside SQLite transactions. Paired comparisons
use the authenticated [first implementation](first-version.py.gz), rather than
an invented slower reference. All results below are host-specific measurements.

| Workload | Before | After | Observed improvement |
| --- | --- | --- | --- |
| Read 64 small persisted summaries | 24.897 ms, individual calls | 0.507 ms, `get_many` | **49.14×** |
| Encode and commit 64 summaries | 38.729 ms, individual calls | 1.879 ms, atomic `set_many` | **20.61×** |
| Eight independent cold CPU producers | 304.40 ms, one active producer | 66.96 ms, eight overlapping producers | **4.55×** |
| Actual consumer corpus loader, 16,000 cases | 957.9 ms cold | 243.8 ms in another warm process | **3.93×**, zero loader calls |

The [batch proof](batches.md) retains nine paired rounds and checks actual return
values and persisted contents after each pair. The [parallel-fill proof](parallel-fills.md)
retains three paired rounds and every worker's producer interval, showing peak
overlap of one before and eight after in every round. The [Consumer proof](consumer-loader.md)
runs the real unchanged loader, includes content/source hashing, and verifies
misses after input and validator-version changes.

Product tests prove the associated contracts independently of wall time:
snapshot consistency across query chunks, complete batch rollback, same-key
exclusion, unrelated write progress, ordered clearing, timeout cleanup, and
fork/crash ownership. The [package guide](../../../src/unstd/cache/README.md)
defines memory, stripe-collision, upgrade and filesystem limits.

## Historical first implementation evidence

The following captures belong to the frozen first implementation; they remain
available as historical receipts. The current comparisons above fingerprint
both the new cache and its lock implementation.

[`cache_reuse.py`](../../profiles/cache_reuse.py) and
[`reuse.json`](reuse.json) separately demonstrate the application use case:
repeated expensive JSON production is replaced by persisted reads in a second
process. These use-case measurements are distinct from the API operation costs
below.

The [Consumer source review](consumer-use-case.md) identifies completed evaluation
summaries and validated corpora as prospective consumers, with explicit input
and version keys. The measurements here use synthetic fixtures.

The [validation record](validation.md) includes product and repository checks,
the baseline failure comparison, and an [installed-wheel probe](installed.json)
in a pristine environment containing only unstd.

## Persisted eval-summary reuse

The proof generates 64 compressed eval exports containing 131,072 rows and
45,796,855 decoded bytes. A cold process decompresses, decodes, and aggregates
each export through existing unstd APIs, caching its JSON summary. A second
process opens the same database and retrieves all summaries. Fixture preparation
and process startup are outside timing; cache initialization, reads, cold
production, and committed writes are timed. No sleeps simulate producer cost.

| Run | Producer calls | Wall time | Result |
| --- | --- | --- | --- |
| Cold, process 10825 | 64 | 120.16 ms | Every summary matches independently accumulated fixture expectations. |
| Warm, process 10836 | 0 | 25.63 ms | Every summary matches expectations and the cold output digest. |

The observed warm run was 4.69× faster on this host and workload. The
stronger functional result is zero repeated producers after reopening the
database in another process. A real cache key must include all producer inputs;
the warm run intentionally skips source reads. Concurrent cold callers are
covered separately by the
[process integration tests](../../../tests/suites/product/cache/integration/test_processes.py).

```console
uv run --no-sync python -m bench.profiles.cache_reuse --batches 64 --rows 2048 --output .local/cache-reuse.json
```

## Historical first-implementation operation costs

[`operations.json`](operations.json) records eight output-checked cache workloads
on CPython 3.14.9, macOS arm64, Apple M5 Max, using a disposable local SQLite WAL
database and full synchronization. The existing harness audits each operation's
result before and after timing. Set, delete, and clear additionally check
persisted state; clear checks that another namespace survives. A warm-compute
producer raises if it is unexpectedly invoked.

```console
uv run --no-sync python -m bench.speeds run --group cache --rounds 7 --output .local/cache-operations.json
uv run --no-sync python -m bench.speeds check --group cache
```

Each fixture contains one name, one boolean, and a list of 32 integers. The
reported latency is the minimum of seven calibrated batch means, following the
existing timing policy. Raw wall/CPU samples, iterations, environment, loaded
source hashes, and workload hashes are retained in the JSON report.

| Operation | Reported latency | Timed work |
| --- | --- | --- |
| Construct | 450.4 µs | Validate an existing database in an empty namespace. |
| `get` hit | 421.7 µs | Open a connection, read, and decode JSON. |
| `get` miss | 540.4 µs | Open a connection and return the caller's default. |
| `set` | 642.6 µs | Encode and replace one committed record. |
| `delete` | 1.57 ms | Set then delete an existing record, including setup. |
| `clear` | 2.75 ms | Set two records then clear their namespace, including setup. |
| `get_or_compute` hit | 502.8 µs | Read and decode; the producer must remain uncalled. |
| `get_or_compute` miss | 1.38 ms | Delete, produce, encode, and commit, including deletion. |

These cases have substantial shared-host timing spread; the raw report keeps
those rounds. These are storage-operation costs on this worker, not speedups
over another cache or portable disk-latency guarantees. The value of the new
use case is avoiding repeated expensive producers; its functional and process
coordination contracts live in the package's product tests.

The eight additive entries in [`speeds.json`](../../baselines/speeds.json)
retain this capture environment and the raw report's hash. All 270 preexisting
case rows, their ceilings and capture provenance, and the global historical
environment are preserved. The operations workload fingerprint advances for
the reviewed cache additions. Whole-registry comparisons still require a
baseline captured on the comparison worker.
