# Focused performance profiles

These drivers compare saved and current implementations, check outputs and import provenance, and retain timings separately from profiling. Run `python -m bench.profiles.NAME --help`. The earlier `python -m bench.NAME` and `python bench/NAME.py` entry points forward here.

- [`serde_profile.py`](serde_profile.py)
- [`jsonx_throughput.py`](jsonx_throughput.py)
- [`jsonx_existing.py`](jsonx_existing.py) — three saved sources, unchanged JSONX registry/core fixtures and separate Python profiles; run this file directly to select sources before import
- [`clone_ids_profile.py`](clone_ids_profile.py)
- [`clone_cpu.py`](clone_cpu.py) — paired wall and process CPU measurements, including large trees, preserved aliases and cyclic fallback
- [`clone_common_cpu.py`](clone_common_cpu.py) — public Python clone entrypoints on common nested data and dataclasses, against saved native baselines
- [`native_profile.py`](native_profile.py)
- [`proc_backend.py`](proc_backend.py) — paired Linux/macOS process latency, exact captured I/O, source/signature checks, and separate wait-route profiles; run this file directly
- [`billy_proc.py`](billy_proc.py) — source-backed Billy Git, metadata and capture workloads, native/Python/stdlib controls, exact outputs and separate native-route profiles; run this file directly
- [`crypto_hash.py`](crypto_hash.py)
- [`fs_backend.py`](fs_backend.py) — paired filesystem traversal and atomic-write timings with real syncing, independent output checks, source isolation, and profiles
- [`billy_fs.py`](billy_fs.py) — Linux native-write experiments using captured Billy consumers, synthetic payloads, exact browser/notebook reference functions, and real syncing
- [`billy_serde.py`](billy_serde.py)
- [`billy_audio.py`](billy_audio.py) — exact PCM wrapping and raw normalization ports, paired timings, allocation peaks and captured Billy consumer source
- [`gzip_reads.py`](gzip_reads.py) — paired CPU/wall read timings, including bounded-memory repetitive streams at 8, 64 and 256 KiB read sizes; records the selected native backend outside timing
- [`billy_gzip.py`](billy_gzip.py) — frozen Billy consumer receipts, real ijson batches, bounded HTTP reads, two-pass uploads, and journal writes/flushes/replay
- [`billy_gzip_requests.py`](billy_gzip_requests.py) — direct request compression and fresh/warm 64 KiB compressor checkpoints, with strict compressed-byte equality and retained contract failures
- [`gzip_concurrency.py`](gzip_concurrency.py) — independent Billy streams on 1, 2 and 4 warmed executor workers, with isolated installations and aggregate wall throughput
- [`text_native_compare.py`](text_native_compare.py)

For installed clone baselines, add `--infer-native-bindings` to load only the
entrypoints imported by that baseline's own Python wrapper. Use
`--require-native --gc-enabled --max-slowdown 1.10` to prove successful native
routes and gate both wall and process CPU latency across all 30 common workloads.
The guard takes the median of matched current/before batch ratios, retaining
independent latency medians for inspection. The output report is saved before
a failed guard exits.

Run the Billy gzip comparison through its direct file entry point so neither
product installation loads before a worker selects its source:

```console
python bench/profiles/billy_gzip.py run --fixtures bench/workloads/billy_gzip.json.gz --baseline-source /tmp/installed-before --source /tmp/installed-current --rounds 7 --repetitions 3 --max-slowdown 1.30 --output /tmp/billy-gzip.json
```

Both workers share the invoking interpreter and dependencies. The committed
synthetic fixtures need no Billy checkout; their source receipts document the
consumer contract. The optional slowdown limit compares wall time and writes
the raw report before failing. CPU samples remain diagnostic.

Measure Billy's separate request encoder and prefix-copy workloads with:

```console
python bench/profiles/billy_gzip_requests.py run --fixtures bench/workloads/billy_gzip_requests.json.gz --baseline-source /tmp/installed-before --source /tmp/installed-current --require-native --rounds 9 --batch-ms 20 --output /tmp/billy-gzip-requests.json
```

This profile leaves the original 14 stream fixtures unchanged. It captures
`gzip_encode`, `gzip_request`, and `_GzipPrefix` source receipts, and measures
public synthetic request bodies with direct level-six compression, first-use
checkpoint construction, warmed copies, and level/prefix misses. Header mapping,
context-variable lookup and network work are excluded. Each checkpoint must
produce exactly its own codec's monolithic compressed bytes, with independent
stdlib decoding. A failed contract marks that case ineligible; other cases are
still measured, and the retained report then exits unsuccessfully. Repeat
`--case` globs to select an explicit subset. Native provider paths, versions and
binary hashes accompany the per-case C-call proof. No compression-level mapping
or relaxed byte oracle is used.

Measure concurrent upload streams through the direct entry point:

```console
python bench/profiles/gzip_concurrency.py run --fixtures bench/workloads/billy_gzip.json.gz --baseline-source /tmp/installed-before --source /tmp/installed-current --workers 1,2,4 --rounds 7 --output /tmp/gzip-concurrency.json
```

Each worker job opens independent streams over the same frozen fixture. Thread
creation and correctness checks remain outside timing; calibrated batches
report total logical expanded bytes over wall time, counting both upload
passes. Before/current order and worker counts alternate across rounds. Use
`--case dataset-query-40001` or a named journal fixture to inspect another
consumer. Raw samples, native-call proof, source/binary hashes and process CPU
diagnostics are retained. This profile has no CI speed gate; aggregate stream
throughput does not establish scaling for every JSON consumer.

See the [benchmark guide](../README.md).

To check JSON packaging or a server build, compare two installed roots using
the same interpreter and dependencies:

```console
python -m bench.profiles.jsonx_throughput --compare-source /tmp/installed-before --source /tmp/installed-current --require-native --include records-1k --rounds 7 --repetitions 3 --max-slowdown 1.15 --output /tmp/json-records.json
```

`--require-native` rejects a missing/wrong native binary, detached bindings or
inactive CPython 3.14 dispatch, and exercises native integer encoding and NDJSON
decoding before timing. The full current implementation also has a separate
[`json_acceleration.py`](../../tests/tooling/runtime/json_acceleration.py)
qualification for its newer float, ASCII and strict-identity paths. Remove
`--include` to measure the complete workload set. `--max-slowdown` requires a
comparison and retains the report before failing; measurements compare wall
latency on one host, rather than assuming different machines have equal speed.
Each fresh comparison worker retains both independent installations, checks
their native bindings before and after timing, and alternates adjacent batches
on one thread. All seven rounds and three repetitions retain their wall/CPU
samples and both stdlib controls; the 15% limit uses matched wall-time ratios.

The JSON profile separately labels shared-row and equal-valued independent-row
matrices. Use `--include floats-256` to compare those eight cases on both
installations. The original 50 workloads retain their inputs and names. Shared
rows permit reuse only within one serialization call; every timed call still
allocates the complete output, and mutation checks reject stale cross-call
results. Report structural-sharing gains separately from independent records.
The gate takes the median of matched after/before ratios from each alternating
repetition. Reports retain all pairs, raw wall/CPU samples and independent
latency medians, so drift and persistent slowdowns remain visible.

Current `deep` workloads require shared-reference preservation; saved historical
baselines retain their earlier alias-duplication checks. `asdict` keeps its
separate-copy behavior. Saved native manifests can identify the graph copier
with `native_graph_entrypoint` so the installed kernel cannot leak into a
historical comparison.
