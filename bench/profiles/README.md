# Focused performance profiles

These drivers compare saved and current implementations, check outputs and import provenance, and retain timings separately from profiling. Run `python -m bench.profiles.NAME --help`. The earlier `python -m bench.NAME` and `python bench/NAME.py` entry points forward here.

- [`serde_profile.py`](serde_profile.py)
- [`clone_ids_profile.py`](clone_ids_profile.py)
- [`clone_cpu.py`](clone_cpu.py) — paired wall and process CPU measurements, including large trees, preserved aliases and cyclic fallback
- [`clone_common_cpu.py`](clone_common_cpu.py) — public Python clone entrypoints on common nested data and dataclasses, against saved native baselines
- [`native_profile.py`](native_profile.py)
- [`rand_backend.py`](rand_backend.py) — saved/current bulk, scalar and construction timings with exact values and full engine-state checks
- [`billy_rand.py`](billy_rand.py) — captured consumer slices with unchanged streams, separate profiles and explicit speedup gates
- [`rand_keyed.py`](rand_keyed.py) — opt-in per-key integer count batches and complete captured consumer costs, with separate target results
- [`rand_strategies.py`](rand_strategies.py) — prospective sparse sampling through the existing public API, labelled independently from backend acceleration
- [`crypto_hash.py`](crypto_hash.py)
- [`fs_backend.py`](fs_backend.py) — paired filesystem traversal and atomic-write timings with real syncing, independent output checks, source isolation, and profiles
- [`billy_fs.py`](billy_fs.py) — historical Linux native-write experiment using captured consumers, synthetic payloads, exact browser/notebook reference functions, and real syncing
- [`billy_fs_installed.py`](billy_fs_installed.py) — built Linux public `atomic_write` versus its Python fallback, with wheel hashes and accepted native-routing proof for every workload
- [`billy_serde.py`](billy_serde.py)
- [`billy_audio.py`](billy_audio.py) — exact PCM wrapping and raw normalization ports, paired timings, allocation peaks and captured Billy consumer source
- [`gzip_reads.py`](gzip_reads.py)
- [`text_native_compare.py`](text_native_compare.py)

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
`--require-native --gc-enabled --max-slowdown 1.30` to prove successful native
routes and gate both median wall and process CPU latency across all 25 common
workloads. The output report is saved before a failed guard exits.

See the [benchmark guide](../README.md).

Current `deep` workloads require shared-reference preservation; saved historical
baselines retain their earlier alias-duplication checks. `asdict` keeps its
separate-copy behavior. Saved native manifests can identify the graph copier
with `native_graph_entrypoint` so the installed kernel cannot leak into a
historical comparison.
