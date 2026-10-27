# Batch reads and atomic batch writes

`get_many` and `set_many` amortize the connection and commit costs of processing
many completed JSON results. The public APIs also provide a single read snapshot
and an atomic write boundary for the whole batch.

[`batches.json`](batches.json) compares the final implementation against the
[frozen first-version source](first-version.py.gz), authenticated by SHA-256
`de9162d4565f51fffd99115af8981d211ef099a8d2b4d449f4df37e94f213428`.
The historical class loads into a separate module with its own globals; no
candidate globals or implementations are monkeypatched. Both versions share
the current serialization and filesystem helpers.

The fixture contains 64 synthetic completed-evaluation summaries. Each value
has an integer ID, a floating-point score, a boolean, and 32 integer counts.
The first version performs one public `get` or `set` call per key. The current
version performs one `get_many` or `set_many` call for all 64 keys.

| Work per application batch | First version | Current batch API | Observed speedup |
| --- | --- | --- | --- |
| Read and decode 64 persisted summaries | 24.897 ms | 0.507 ms | 49.14× |
| Encode and commit 64 summaries | 38.729 ms | 1.879 ms | 20.61× |

These are medians of nine paired wall-clock samples on CPython 3.14.9,
macOS arm64. Execution order alternates between rounds. The raw report retains
every sample and source hashes for the historical implementation, current cache,
current lock implementation, shared JSON codec, and proof driver.

Constructor calls and fixture preparation occur outside timing. Every timed
read decodes all 64 values; its actual return value is checked against expected
JSON after timing. Every timed write encodes all 64 values and includes a full
synchronized commit. The candidate also acquires its bounded key-lock stripes.
After every pair, both databases are read again outside timing to verify all
persisted values. No sleeps simulate expensive work.

```console
uv run --no-sync python -m bench.profiles.cache_batches --entries 64 --rounds 9 --output .local/cache-batches.json
```

The performance result applies to this host, fixture size, and comparison of
per-key operations with batch operations. It does not establish a speedup for
single-key requests, every storage device, or every application. The package's
unit tests separately verify atomic rollback on a later database error,
preflight encoding failure, consistent snapshots across chunked queries during
a concurrent commit, common expiry boundaries, namespace isolation, and FIFO
retention.

[`batch-operations.json`](batch-operations.json) separately captures the two new
official operation-registry workloads using the existing calibrated timing
estimator, correctness audits, and raw wall/CPU samples. Only their two new
measurement rows and capture receipt were appended to the historical baseline.
All prior rows, including the first version's eight cache operation rows and
their ceilings, remain unchanged. These operation costs and the paired median
comparison above are separate measurements with separately recorded fixtures.
