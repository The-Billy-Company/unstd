# Focused performance profiles

These drivers compare saved and current implementations, check outputs and import provenance, and retain timings separately from profiling. Run `python -m bench.profiles.NAME --help`. The earlier `python -m bench.NAME` and `python bench/NAME.py` entry points forward here.

- [`serde_profile.py`](serde_profile.py)
- [`jsonx_throughput.py`](jsonx_throughput.py)
- [`jsonx_existing.py`](jsonx_existing.py) — three saved sources, unchanged JSONX registry/core fixtures and separate Python profiles; run this file directly to select sources before import
- [`clone_ids_profile.py`](clone_ids_profile.py)
- [`clone_cpu.py`](clone_cpu.py) — paired wall and process CPU measurements, including large trees, preserved aliases and cyclic fallback
- [`clone_common_cpu.py`](clone_common_cpu.py) — public Python clone entrypoints on common nested data and dataclasses, against saved native baselines
- [`native_profile.py`](native_profile.py)
- [`crypto_hash.py`](crypto_hash.py)
- [`billy_serde.py`](billy_serde.py)
- [`billy_audio.py`](billy_audio.py) — exact PCM wrapping and raw normalization ports, paired timings, allocation peaks and captured Billy consumer source
- [`gzip_reads.py`](gzip_reads.py)
- [`billy_gzip.py`](billy_gzip.py) — frozen Billy consumer receipts, real ijson batches, bounded HTTP reads, two-pass uploads, and journal writes/flushes/replay
- [`gzip_concurrency.py`](gzip_concurrency.py) — independent Billy streams on 1, 2 and 4 warmed executor workers, with isolated installations and aggregate wall throughput
- [`text_native_compare.py`](text_native_compare.py)

Run the Billy gzip comparison through its direct file entry point so neither
product installation loads before a worker selects its source:

```console
python bench/profiles/billy_gzip.py run --fixtures bench/workloads/billy_gzip.json.gz --baseline-source /tmp/installed-before --source /tmp/installed-current --rounds 7 --repetitions 3 --max-slowdown 1.30 --output /tmp/billy-gzip.json
```

Both workers share the invoking interpreter and dependencies. The committed
synthetic fixtures need no Billy checkout; their source receipts document the
consumer contract. The optional slowdown limit compares wall time and writes
the raw report before failing. CPU samples remain diagnostic.

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

Current `deep` workloads require shared-reference preservation; saved historical
baselines retain their earlier alias-duplication checks. `asdict` keeps its
separate-copy behavior. Saved native manifests can identify the graph copier
with `native_graph_entrypoint` so the installed kernel cannot leak into a
historical comparison.
