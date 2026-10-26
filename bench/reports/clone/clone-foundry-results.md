# Native clone qualification on the Foundry

Measured 2026-10-25 on the Linux x86_64 Foundry host, using separately compiled CPython 3.13.16 and 3.14.8 native extensions. Native graph, mixed dataclass and `asdict` execution remains active through the public Python APIs. Ordinary 200-dataclass workloads retain **2.33–2.47×** the historical baseline’s CPU speed; the 20,000-record case retains **2.57–6.28×**.

The full installed-wheel clone suites pass: **446 tests on 3.13** and **444 on 3.14**. A separate forced-GC/allocation-failure audit passes **69 tests**. The ordinary suites skip instrumented-audit cases; the forced-GC campaign skips its source-marker-only case. No clone runtime source changes were needed.

Some common builtin graphs retain the slowdowns already seen in the [Mac follow-up](clone-common-cpu-results.md). The worst final server measurement is repeated aliases on 3.14 with GC enabled: **18.2% more CPU time and wall time** than the historical baseline. The slotted-dataclass protocol fallback is sometimes slower than stdlib. These results do not establish uniform speedups for every shape.

## Build and comparison

The baseline is `31deedf072af28d0cd535801d79da5974321543a`, the same shared-identity implementation used for the Mac common-data report. Current clone sources match `6ef26acd9156bc12a2731f8651ab3a7dbcff7391`. Both sides retain shared identities. The current installation comes from a wheel built with `CIBUILDWHEEL=1`; the baseline wrapper and its own C source were compiled separately for each interpreter using the same interpreter/compiler settings.

All timing calls use public `deep`/`asdict`, including wrapper dispatch and output destruction. Each of the 25 workloads is checked for exact output, mutable isolation, atomic identity and alias semantics before calibration. Nine rounds alternate implementation order and retain both process CPU and wall samples. Each configuration runs in a separate process, pinned to CPU 29 in a one-CPU, 4 GiB container. The Foundry was also running CI work; paired ratios are the useful comparison, and absolute Mac/Linux times are not treated as equivalent. Allocation layout can materially change the old pointer table’s clustering and its measured large-graph gain.

Successful native return tracing verifies these representative public routes on both versions:

| Public workload | Current | Baseline |
| --- | --- | --- |
| Builtin graph with aliases | `_native_graph` | `_native_graph` |
| Mixed dataclass graph with aliases | `_native_walk_graph` | `_native_dataclass` |
| Positional `asdict` expansion | `_native_clone` | `_native_clone` |

Baseline imports are inferred from that baseline’s own `structural.py` and rebound to its own ABI-matched extension. Explicit manifest declarations take precedence. Missing modules, Python-only routes and native refusals fail qualification. The [receipt](clone-foundry-receipt.json) records exact source, harness, wheel and extension provenance, compiler settings, test counts and report hashes.

## Public API CPU gains

Ratios are baseline/current median CPU time. Below 1× means current is slower. The final two columns compare current against `copy.deepcopy` or `dataclasses.asdict`, with GC enabled.

| Workload | 3.13 GC off | 3.13 GC on | 3.14 GC off | 3.14 GC on | 3.13 vs stdlib | 3.14 vs stdlib |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 1.02× | 0.98× | 0.97× | 1.02× | 8.37× | 5.22× |
| nested-200 | 0.90× | 0.95× | 0.99× | 1.01× | 9.18× | 7.04× |
| records-500 | 0.98× | 0.96× | 0.94× | 1.01× | 12.70× | 8.29× |
| tuple-records-500 | 0.95× | 0.99× | 1.34× | 0.91× | 15.88× | 12.58× |
| wide-lists-1000 | 0.91× | 0.95× | 0.97× | 1.06× | 9.06× | 7.05× |
| deep-80 | 0.95× | 0.96× | 1.07× | 0.87× | 5.22× | 4.96× |
| large-records-20000 | 2.94× | 2.57× | 3.30× | 6.28× | 10.67× | 4.15× |
| repeated-aliases-500 | 0.90× | 0.87× | 0.88× | 0.85× | 9.93× | 8.90× |
| dataclasses-200 | 2.47× | 2.45× | 2.33× | 2.36× | 6.59× | 5.15× |
| dataclass-records-500 | 0.91× | 0.96× | 0.88× | 0.87× | 5.40× | 3.44× |
| mixed-keys-1000 | 2.43× | 2.34× | 2.28× | 2.28× | 5.34× | 3.38× |
| foreign-last | 2.29× | 2.35× | 2.72× | 3.02× | 11.14× | 4.69× |
| asdict-records-500 | 1.00× | 0.98× | 0.97× | 0.91× | 3.62× | 3.60× |
| nested-small-5 | 0.91× | 0.95× | 0.92× | 0.94× | 9.48× | 6.47× |
| nested-wide-1000 | 0.94× | 0.93× | 0.86× | 1.26× | 8.58× | 6.70× |
| balanced-dict-list | 0.94× | 0.99× | 0.91× | 0.98× | 10.23× | 7.54× |
| dictionary-of-records-500 | 1.01× | 2.93× | 2.40× | 2.59× | 8.64× | 5.58× |
| dataclass-small | 1.05× | 1.12× | 1.05× | 0.98× | 2.47× | 1.75× |
| dataclasses-copy-hooks-40 | 2.32× | 2.35× | 2.15× | 2.20× | 3.13× | 2.45× |
| dataclasses-frozen-200 | 2.23× | 2.50× | 2.40× | 2.11× | 6.35× | 5.03× |
| dataclasses-slots-200 | 1.04× | 1.07× | 0.97× | 1.01× | 0.96× | 0.90× |
| dataclasses-flat-500 | 3.67× | 2.63× | 2.70× | 2.64× | 7.41× | 5.56× |
| dataclasses-datetime-200 | 1.99× | 2.18× | 2.26× | 2.03× | 4.43× | 3.71× |
| dataclass-tree-200 | 2.47× | 2.50× | 2.20× | 2.37× | 6.62× | 4.86× |
| asdict-tree-200 | 0.99× | 1.01× | 0.98× | 0.98× | 1.85× | 1.92× |

Raw measurements: [3.13 GC off](clone-foundry-py313.json), [3.13 GC on](clone-foundry-py313-gc.json), [3.14 GC off](clone-foundry-py314.json), [3.14 GC on](clone-foundry-py314-gc.json).

## Ongoing guard

The performance workflow now checks all 25 shapes with GC enabled. The Foundry qualification covers Python 3.13 and 3.14. Both median wall and CPU latency must remain within 130% of the same worker’s comparison revision. The current Foundry runs pass all four configurations. This allowance is explicit; it permits the smaller regressions recorded above. Native-route loss fails before timing. JSON evidence is written before a failed guard exits.

For separately installed before/current wheels in a shared dependency environment:

```console
PYTHONPATH=/tmp/installed-current:$PWD /path/to/shared/python -m bench.profiles.clone_common_cpu --compare-dir /tmp/installed-before/unstd/clone --infer-native-bindings --require-native --gc-enabled --rounds 9 --max-slowdown 1.30 --output /tmp/clone-common.json
```

The two installed products must be built with `CIBUILDWHEEL=1`, the same compiler/interpreter and no borrowed native binaries. Repeat without `--gc-enabled` for the collector-disabled control.

Validation logs: [3.13 product](clone-foundry-py313-tests.txt), [3.14 product](clone-foundry-py314-tests.txt), [forced-GC campaign](clone-foundry-gc-tests.txt). Local benchmark/workflow guards pass **66 tests**; Ruff and the profile’s strict Mypy check pass.
