# Actual Consumer corpus-loader reuse

[`consumer_cache.py`](../../profiles/consumer_cache.py) imports and executes Consumer's
actual `benchmarks/harness/loaders/fixtures.py:load_tool_use_corpus` function.
The source is loaded as a complete, unchanged module from an explicit Consumer
checkout. No extracted implementation, substitute validator, live responses,
or application data are used.

The generated corpus contains 16,000 valid cases, each with eight tools and
one expected tool call: 128,000 tool validations per cold call. Comments and
blank lines also exercise the loader's JSONL framing. Each process compares
the complete result against independently constructed expected rows.

```console
uv run --no-sync python -m bench.profiles.consumer_cache --consumer-source /path/to/checkout --rows 16000 --tools 8 --output .local/consumer-loader.json
```

The supplied Consumer source must be compatible with the selected interpreter and
its own module imports. This capture uses Python 3.14 and the existing
development environment; it adds no runtime dependency to unstd.

The recorded 28,606,709-byte corpus produced the following result on this
macOS arm64 worker:

| Process | Producer calls | Time | Independently checked result |
| --- | --- | --- | --- |
| Cold, PID 30598 | 1 | 957.9 ms | All 16,000 rows match expected values. |
| Warm, PID 30728 | 0 | 243.8 ms | All rows and the cold output digest match. |
| Changed input, PID 30773 | 1 | 949.2 ms | Changed first query appears in the result. |
| Changed validator version, PID 30848 | 1 | 920.9 ms | Original rows match under a new cache key. |

The observed warm run was **3.93× faster** while still hashing all source-input
bytes. The source-backed functional result is zero repeated parsing or
validation in the warm process, with explicit misses for input and validation
version changes. These host-specific elapsed times are diagnostic, not a
portable latency guarantee.

Four fresh processes use the same persistent cache:

1. The cold process parses and validates the actual corpus, then stores its JSON.
2. A new warm process reuses the result with zero loader invocations.
3. A changed-input process modifies the first query and must invoke the loader.
4. A changed-validator-version process uses the original input under a new
   explicit validation version and must invoke the loader again.

Keys contain the exact corpus-content digest, the complete loader-source
digest, and the explicit validator version. Source and input hashing are
included in each timer, so the warm path still pays to read all input bytes.
Input edits and validator-version changes produce separate cache entries. A
production caller can instead use a trusted immutable input manifest to avoid
rehashing, while preserving the same key identity.

Fixture generation, expected-row construction, module import, process startup,
and output comparisons/digests are outside timing. Source/input hashing, cache
initialization, lookup, JSON decoding, and cold parsing/validation/encoding and
committed writes are inside timing. No sleeps simulate expensive work.

[`consumer-loader.json`](consumer-loader.json) retains the observed times, distinct
process IDs, exact producer counts, matching independent output digests,
invalidation results, host details, and hashes of the actual Consumer loader,
cache sources, and proof driver. These results concern synthetic input through
real Consumer code on one machine. They do not measure a deployed Consumer service or
claim that Consumer application code was migrated to this package.

See the [package guide](../../../src/unstd/cache/README.md) for cache expiry,
capacity, process coordination, and error contracts.
