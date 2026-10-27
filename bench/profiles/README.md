---
doc_radar:
  sentinels:
    - file: bench/profiles/clone_common_cpu.py
      contains: ['"--consumer-cases"', '"--temporal-cases"', '"--include"', '"--source-dir"', '"consumer_workloads_sha256"', '"temporal_workloads_sha256"', '"model_warmup"']
    - file: bench/workloads/clone_consumers.py
      contains: ['"consumer-attachment-images-10"', '"consumer-change-slots-1000"', '"consumer-question-private-alias"']
    - file: bench/workloads/clone_temporals.py
      contains: ['"temporal-mixed-scalars-1000"', '"temporal-tuple-date-1000"', '"temporal-nested-date-1000"', '"temporal-scalar-first-1000"']
---

# Focused performance profiles

These drivers compare saved and current implementations, check outputs and import provenance, and retain timings separately from profiling. Run `python -m bench.profiles.NAME --help`. The earlier `python -m bench.NAME` and `python bench/NAME.py` entry points forward here.

- [`serde_profile.py`](serde_profile.py)
- [`jsonx_throughput.py`](jsonx_throughput.py)
- [`jsonx_existing.py`](jsonx_existing.py) — three saved sources, unchanged JSONX registry/core fixtures and separate Python profiles; run this file directly to select sources before import
- [`clone_ids_profile.py`](clone_ids_profile.py)
- [`containers.py`](containers.py) — isolated before/current sorted-query, immutable-map and cold-import measurements, with native dispatch checks
- [`containers_mutations.py`](containers_mutations.py) — paired scalar and batch mutations, retained/exclusive ID ownership controls, and actual native mutation counters
- [`clone_cpu.py`](clone_cpu.py) — paired wall and process CPU measurements, including large trees, preserved aliases and cyclic fallback
- [`clone_common_cpu.py`](clone_common_cpu.py) — public Python clone entrypoints on common nested data and dataclasses, against saved native baselines
- [`native_profile.py`](native_profile.py)
- [`ragged.py`](ragged.py) — complete captured numeric-frame decoding with fused validation, immutable output, exact result-class construction, native-route proof and an explicit speedup gate
- [`pack_backend.py`](pack_backend.py) — scalar/typed-array packing, owned-list and zero-copy decoding, with saved-source comparisons and exact wire checks
- [`pack_consumers.py`](pack_consumers.py) — complete captured buffer decode/publication operations and explicit prospective API choices
- [`rand_backend.py`](rand_backend.py) — saved/current bulk, scalar and construction timings with exact values and full engine-state checks
- [`billy_rand.py`](billy_rand.py) — captured consumer slices with unchanged streams, separate profiles and explicit speedup gates
- [`rand_keyed.py`](rand_keyed.py) — opt-in per-key integer count batches and complete captured consumer costs, with separate target results
- [`rand_strategies.py`](rand_strategies.py) — prospective sparse sampling through the existing public API, labelled independently from backend acceleration
- [`proc_backend.py`](proc_backend.py) — paired Linux/macOS process latency, exact captured I/O, source/signature checks, and separate wait-route profiles; run this file directly
- Process consumer profile — source-backed Git, metadata and capture workloads, native/Python/stdlib controls, exact outputs and separate native-route profiles; run the driver directly
- [`crypto_hash.py`](crypto_hash.py)
- [`fs_backend.py`](fs_backend.py) — paired filesystem traversal and atomic-write timings with real syncing, independent output checks, source isolation, and profiles
- [`billy_fs.py`](billy_fs.py) — historical Linux native-write experiment using captured consumers, synthetic payloads, exact browser/notebook reference functions, and real syncing
- [`billy_fs_installed.py`](billy_fs_installed.py) — built Linux public `atomic_write` versus its Python fallback, with wheel hashes and accepted native-routing proof for every workload
- Serde consumer profile — saved-fixture comparisons against isolated package installations
- Audio consumer profile — exact PCM wrapping and raw normalization ports, paired timings, allocation peaks and captured consumer source
- [`gzip_reads.py`](gzip_reads.py) — paired CPU/wall read timings, including bounded-memory repetitive streams at 8, 64 and 256 KiB read sizes; records the selected native backend outside timing
- Gzip consumer profile — frozen consumer receipts, real ijson batches, bounded HTTP reads, two-pass uploads, and journal writes/flushes/replay
- [`billy_gzip_requests.py`](billy_gzip_requests.py) — 11 frozen request and dataset encode cases; direct gzip/JSON/base64 operations and fresh/warm 64 KiB compressor checkpoints, with strict checkpoint equality and retained contract failures
- [`gzip_concurrency.py`](gzip_concurrency.py) — independent consumer streams on 1, 2 and 4 warmed executor workers, with isolated installations and aggregate wall throughput
- [`text_native_compare.py`](text_native_compare.py)
- [`text_affixes.py`](text_affixes.py) — saved/current edit distance on near-identical strings and unrelated controls, through isolated native and source-only wrappers; retains dispatch, exact outputs, paired timings and allocation peaks

Run `pack_backend.py`, `pack_consumers.py`, and `ragged.py` directly so product
source selection happens before imports. The ragged driver includes complete
numeric validation, array creation, and captured result-class allocation;
`--require-native` checks the fused route. `--enforce-target --target-speedup 100`
writes the evidence and then exits nonzero when the complete-decoder target is
not met. Read its [report](../reports/pack/ragged/README.md) before interpreting
the array-selection-only results from the earlier consumer profile.

For filesystem backend work, use the direct file entry point of
[`billy_fs_installed.py`](billy_fs_installed.py) inside Linux. Select one built,
installed package root and its wheel; both lanes load identical fs bytes from
that root. One public module keeps native dispatch and the other sets
`_native=None`. The checked [Billy fixtures](../workloads/billy_fs.json) require
the captured consumer checkout for source hashes and reference ASTs, without
importing application dependencies.

```console
python bench/profiles/billy_fs_installed.py --source /tmp/fs-install --wheel /tmp/fs-dist/unstd-3.1.6-cp314-cp314-linux_aarch64.whl --fixtures bench/workloads/billy_fs.json --billy-source ../billy --root /tmp --rounds 9 --output /tmp/fs-installed.json
```

Use the wheel's actual ABI/platform filename. Before and after each workload,
untimed profile hooks require an actual C call with zero Python fallbacks in the
native lane and Python fallback calls with zero native entries in the control.
Sentinel resets and independent byte/mode/scratch checks reject declined or
no-op writers. Loaded Python/binary files must match the supplied wheel, and
source, fixture, harness and binary hashes are checked again after timing.
Calibration and separate cProfile runs stay outside alternating wall/CPU
samples; real fsync remains inside the measured operations.

The [installed qualification](../reports/fs/production/README.md) retains direct
wheel and sdist-rebuilt wheel results, cancellation/error coverage, and fallback
packaging checks for its recorded source snapshot. These are synthetic workload
shapes, not service-frequency weights; disk regressions remain visible and
tmpfs measures CPU/VFS overhead. Later source edits require fresh evidence.
The [historical experiment](../reports/fs/billy-linux.md) uses a separately built
`_fs_experiment` binary through `billy_fs.py run --native-extension ...`; its
comparison lane explicitly forces Python even when public native dispatch is
available. Its ratios and admission limits are separate from installed results.

For installed clone baselines, add `--infer-native-bindings` to load only the
entrypoints imported by that baseline's own Python wrapper. Use
`--require-native --gc-enabled --max-slowdown 1.10` to prove successful native
routes and gate both wall and process CPU latency across all 30 common workloads.
The guard takes the median of matched current/before batch ratios, retaining
independent latency medians for inspection. The output report is saved before
a failed guard exits. Calibration targets 50 ms batches using the fastest of
three probes, so one cold call cannot select a tiny steady-state batch. This
chooses iteration counts only; all nine measured rounds retain collector and
scheduling costs with GC enabled.

Add `--consumer-cases --include 'consumer-*'` to measure the synthetic question,
prepared-image, material and slotted-comparison records. These optional cases
need Pydantic; their checks include private model state, graph aliases, cyclic
fallback and foreign copy hooks, while the original common cases stay unchanged.
These consumer comparisons require baselines that preserve shared references.
Every implementation first copies the same private/public-alias model through
its public API and passes the strict check, even when filters select non-model
cases. The report records warmed labels, backend capabilities and marker state
so model activation is consistent across saved candidates.
Use `--source-dir PATH` to measure a frozen candidate snapshot through the same
isolated loader as `--compare-dir`, without timing an editable source file.
`--target-speedup 100` records the remaining gap to a 100× stdlib comparison for
both matched clocks. `--enforce-target` fails after saving the report when any
selected workload misses that target; existing regression limits stay separate.

Add `--temporal-cases --include 'temporal-*'` for ten distinct-value date,
naive/UTC datetime and naive/UTC time lists at 1,000 and 10,000 leaves. Four
controls retain mixed scalars, tuple output, nested lists and a scalar-first
sequence. Current and saved source copies must share the exact immutable
leaves; stdlib may rebuild equal leaves. Containers remain independent.
`--require-native` proves the public closed temporal route only when that
snapshot's own backend advertises it, with successful C graph copying and no
mixed or Python walk. Both optional sets leave the default thirty cases intact.

Run the gzip consumer comparison through its direct file entry point so neither
product installation loads before a worker selects its source. Select the two
installed roots with `--baseline-source` and `--source`, supply the committed
transport fixtures with `--fixtures`, and save evidence with `--output`.

Both workers share the invoking interpreter and dependencies. The committed
synthetic fixtures need no external checkout; their source receipts document the
consumer contract. The optional slowdown limit compares wall time and writes
the raw report before failing. CPU samples remain diagnostic.

The separate request profile measures encoder and prefix-copy workloads.
Use its direct file entry point with the request fixtures and the two installed
roots. `--require-native` checks the selected native route; `--rounds` and
`--batch-ms` control the measurement budget.

This profile leaves the original 14 stream fixtures unchanged. It captures
request encoder and compressor checkpoint source receipts, and measures
public synthetic request bodies with direct level-six compression, first-use
checkpoint construction, warmed copies, and level/prefix misses. Header mapping,
context-variable lookup and network work are excluded. Each checkpoint must
produce exactly its own codec's monolithic compressed bytes, with independent
stdlib decoding. A failed contract marks that case ineligible; other cases are
still measured, and the retained report then exits unsuccessfully. Repeat
`--case` globs to select an explicit subset. Native provider paths, versions and
binary hashes accompany the per-case C-call proof. No compression-level mapping
or relaxed byte oracle is used.

Measure concurrent upload streams through the direct entry point of
[`gzip_concurrency.py`](gzip_concurrency.py). Supply the transport fixtures with
`--fixtures`, the installed roots with `--baseline-source` and `--source`, and
worker counts with `--workers 1,2,4`. Save evidence with `--output`.

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
on one thread. Count calibration targets 50 ms using the fastest of three
probes, with the slower codec's estimated batch still bounded at 250 ms.
All seven rounds and three repetitions retain their wall/CPU
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
