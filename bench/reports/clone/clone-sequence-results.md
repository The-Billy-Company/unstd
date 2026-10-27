# Clone sequence CPU follow-up

Measured 2026-10-25 on Consumer’s Linux x86_64 Foundry through public `clone.deep` and `clone.asdict`, with separately built CPython 3.13.16 and 3.14.8 wheels. The repeated-alias case that previously regressed is now **4.68–4.92× faster than the previous native implementation** and **4.14–4.32× faster than the older shared-identity baseline**. Larger adjacent-alias runs improve **5.89–6.26×** against the previous native implementation.

Scalar arrays improve **1.05–1.17×** on this server. The largest observed stdlib gain is **83.0×**, for `repeated-aliases-100000` on Python 3.13 with GC disabled. All 30 workloads pass the new 10% wall/CPU guard in all four configurations.

The requested universal 100× target remains unmet. `clone.copy` and `clone.deepcopy` are exact stdlib function aliases, giving a 1× comparison by definition. Slotted dataclasses and arbitrary foreign copy hooks retain their protocol fallbacks. Every measured case, including smaller gains and slowdowns, remains in the table and raw reports.

## Changes

A closed native sequence fills its scalar prefix in an allocation-free loop. Fully scalar sequences return with initialized storage, avoiding per-item recursive-loop bookkeeping. A private builtin graph whose first non-atomic child starts an adjacent repeated run copies that child once, then fills reserved slots with references to its completed memo result. The generic live loop resumes when the run ends or the source grows past the original allocation.

The source and completed result remain owned by the private memo. Source shrink, growth, replacement and mutation of the completed clone retain the normal graph relationships. Caller memos, foreign-node continuations and positional `asdict` copies bypass the adjacent-run shortcut. Incomplete recursive outputs remain hidden from GC observers. There are no new native entrypoints or runtime dependencies.

The benchmark adds scalar lists/tuples and 10,000/100,000-reference alias graphs, bringing the persistent guard to 30 cases. Its 10% limit compares the median of matched current/before batch ratios for both clocks. Independent latency medians remain recorded. Drift fixtures demonstrate why a ratio of independent medians can report a false slowdown; persistent candidate-only delays still fail. Unpaired, empty, zero and nonfinite raw samples fail with evidence retained.

## Measurement

The before runtime is `6ef26acd9156bc12a2731f8651ab3a7dbcff7391`, also underlying the qualification committed as `d9f906a`. The historical control is `31deedf072af28d0cd535801d79da5974321543a`. Both baselines load their own ABI-matched binaries and their own Python imports. The exact measured working-source snapshot, including its dataclass admission cache, is retained in the [snapshot](clone-sequence-snapshot.tar.gz); the [receipt](clone-sequence-receipt.json) records source, harness, wheel, binary and report hashes.

All primary timings include public wrapper dispatch, allocation and ordinary output destruction. Inputs and reference checks are prepared outside timing; admission/field caches are warmed. Fifteen rounds alternate implementation order. GC-on and GC-off configurations use separate processes pinned to logical CPU 28, selected after checking idle CPU time. The Foundry also carried CI load; matched controls and separate wall/CPU samples remain necessary. Absolute Mac/Linux times are not treated as equivalent.

Worst measured current/before ratios across all final cases are **1.064× CPU** and **1.064× wall**. The guard allows up to 1.10×; passing it does not establish exact zero regression. The original pointer table’s allocation-layout sensitivity still makes large-record historical gains variable.

## Paired CPU gains

Ratios are before/current matched CPU batch times. Below 1× means current is slower. Final columns compare with stdlib with GC enabled.

| Workload | 3.13 GC off | 3.13 GC on | 3.14 GC off | 3.14 GC on | 3.13 vs stdlib | 3.14 vs stdlib |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.99× | 1.00× | 1.01× | 1.00× | 9.12× | 5.13× |
| nested-200 | 1.05× | 1.05× | 1.07× | 1.08× | 9.32× | 6.57× |
| records-500 | 1.02× | 1.00× | 0.95× | 1.00× | 14.20× | 7.53× |
| tuple-records-500 | 1.10× | 1.10× | 1.12× | 1.08× | 18.53× | 12.36× |
| wide-lists-1000 | 1.13× | 1.12× | 1.11× | 1.12× | 10.85× | 8.59× |
| deep-80 | 1.01× | 0.99× | 1.05× | 1.01× | 5.50× | 5.48× |
| large-records-20000 | 1.01× | 1.03× | 1.00× | 1.01× | 7.90× | 5.66× |
| repeated-aliases-500 | 4.86× | 4.82× | 4.68× | 4.92× | 49.76× | 51.62× |
| dataclasses-200 | 0.99× | 0.99× | 0.94× | 1.00× | 6.61× | 4.99× |
| dataclass-records-500 | 1.01× | 1.00× | 0.99× | 0.99× | 5.18× | 3.40× |
| mixed-keys-1000 | 1.03× | 1.02× | 1.01× | 0.97× | 5.49× | 3.31× |
| foreign-last | 1.05× | 1.04× | 1.03× | 1.07× | 12.28× | 5.04× |
| asdict-records-500 | 1.03× | 1.03× | 1.11× | 1.09× | 3.95× | 4.26× |
| nested-small-5 | 1.05× | 1.02× | 1.06× | 1.04× | 9.76× | 6.95× |
| nested-wide-1000 | 1.05× | 1.04× | 1.06× | 1.09× | 9.74× | 6.67× |
| balanced-dict-list | 1.02× | 1.03× | 1.02× | 1.01× | 10.62× | 6.99× |
| dictionary-of-records-500 | 1.05× | 1.03× | 1.04× | 1.04× | 9.32× | 5.72× |
| dataclass-small | 1.30× | 1.30× | 1.33× | 1.33× | 3.18× | 2.54× |
| dataclasses-copy-hooks-40 | 1.01× | 1.00× | 0.99× | 1.01× | 3.15× | 2.59× |
| dataclasses-frozen-200 | 1.00× | 0.98× | 0.99× | 0.99× | 6.73× | 5.06× |
| dataclasses-slots-200 | 1.00× | 1.01× | 1.01× | 1.00× | 0.95× | 0.98× |
| dataclasses-flat-500 | 0.99× | 0.98× | 1.01× | 1.00× | 7.57× | 5.36× |
| dataclasses-datetime-200 | 1.00× | 0.99× | 0.99× | 0.99× | 4.54× | 3.83× |
| dataclass-tree-200 | 0.99× | 0.99× | 0.98× | 0.98× | 6.66× | 4.99× |
| asdict-tree-200 | 1.02× | 0.94× | 1.00× | 1.01× | 1.75× | 2.02× |
| scalar-list-1000 | 1.13× | 1.12× | 1.12× | 1.17× | 41.36× | 15.13× |
| scalar-tuple-1000 | 1.07× | 1.05× | 1.11× | 1.15× | 44.21× | 20.29× |
| scalar-list-100000 | 1.14× | 1.16× | 1.16× | 1.15× | 46.98× | 15.81× |
| repeated-aliases-10000 | 6.16× | 6.10× | 5.89× | 5.89× | 57.89× | 65.47× |
| repeated-aliases-100000 | 6.26× | 6.23× | 5.92× | 5.97× | 59.46× | 65.31× |

Raw evidence: [3.13 GC off](clone-sequence-foundry-py313.json), [3.13 GC on](clone-sequence-foundry-py313-gc.json), [3.14 GC off](clone-sequence-foundry-py314.json), [3.14 GC on](clone-sequence-foundry-py314-gc.json). The older `cpu_speedup_vs_*` fields retain ratios of independent latency medians; this table and the new guard use matched raw rounds.

## Validation

The final installed wheels pass **451 product tests on Python 3.14** and **453 on Python 3.13**, with 37/35 instrumentation-dependent skips. The forced-GC/OOM campaign passes **86 tests**, and the same campaign with ASan/UBSan passes **86 tests**, each with one expected Python 3.14 C-stack-counter skip. All 17 new sequence tests pass in the instrumented campaigns. Local product/related checks pass 532 tests; the expanded benchmark guard checks pass 78 tests. Ruff and the profile’s strict Mypy check pass.

Linux sanitizer audits now remove inherited `LD_PRELOAD` after ASan has already loaded in Python. The wrapper reinstates it for every actual Python exec, keeping the native kernel instrumented while avoiding sanitizer injection into intermediate shell/`env` processes. Stock launch controls reproduced the previous shell failure; the retained deliberate heap-overflow positive control still aborts with an ASan report after the fix. The canonical audit builder and native source hashes match the working files.

Logs: [Python 3.14](clone-sequence-py314-tests.txt), [Python 3.13](clone-sequence-py313-tests.txt), [forced GC](clone-sequence-gc-tests.txt), [ASan/UBSan plus forced GC](clone-sequence-asan-tests.txt).

Reproduce with two separately built native installations in one dependency environment:

```console
PYTHONPATH=/tmp/installed-current:$PWD /path/to/shared/python -m bench.profiles.clone_common_cpu --compare-dir /tmp/installed-before/unstd/clone --infer-native-bindings --require-native --gc-enabled --rounds 15 --max-slowdown 1.10 --output /tmp/clone-sequences.json
```

Repeat without `--gc-enabled` for the collector-disabled control. Use the archived source snapshot and matching interpreters/compiler settings for these exact measurements.
