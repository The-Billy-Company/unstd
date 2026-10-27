# Typed artifact operation contracts

[`codec-operations.json`](codec-operations.json) records the two additive
`get_or_compute` codec workloads. They use a structural codec that encodes one
signed 64-bit integer into exactly eight bytes and rejects any other decoded
length. A dedicated versioned namespace and separate database keep artifact
setup out of the existing JSON workload state.

- The warm case validates the persisted integer and raises if its producer is
  unexpectedly called.
- The cold case deletes a key, produces the integer, encodes and decodes it,
  then commits it. Its timer includes deletion and all resource lifecycles.

Both cases use the standard `bench.speeds.timings` calibration and nine rounds,
with correctness audits before and after timing. The report retains raw wall
and thread-CPU samples, actual environment, loaded sources, workload hashes,
and the separate worker baseline. These tiny-artifact costs are operation
contracts; they are not a speedup claim for a consumer's preprocessing workflow
or a portable disk-latency guarantee.

Only two new rows and their capture receipt were added to the aggregate frozen
baseline. All 302 preexisting rows, ceilings, capture metadata, and the global
historical environment/timestamp remain unchanged. Reversing only the codec
workload additions reconstructs the previously recorded operations-provider
fingerprint.

```console
uv run --no-sync python -m bench.speeds run --group cache --rounds 9 --output .local/cache-operations-current.json
uv run --no-sync python -m bench.speeds check --group cache
```

The full cache group also measures the existing JSON and batch operations.
Source-backed end-to-end workflow measurements belong to their own retained
consumer reports, including platform, input, invalidation and failure evidence.
