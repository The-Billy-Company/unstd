# Common clone CPU follow-up

Measured 2026-10-25 on an arm64 Mac through the public Python `clone.deep` and `clone.asdict` functions. Every primary timing includes wrapper dispatch, native admission/refusal/fallback, output allocation and ordinary output destruction. None of these 25 common workloads contains a self-cycle.

Against the completed shared-identity implementation, 200 ordinary dataclasses improve **2.00–2.16×** and 500 scalar dataclasses improve **2.11–2.32×**. A 20,000-record dictionary list improves **13.80–22.78×**, largely by avoiding pointer-table clustering. Nested dict/list records measure **0.88–0.93×** against that already optimized baseline and **5.62–9.38×** against stdlib. These common cases do **not** reach 100×. The complete tables retain regressions and fallback cases.

The paired baseline is the completed main implementation at commit `31deedf072af28d0cd535801d79da5974321543a`, captured before this follow-up. Both sides preserve shared identities within each `deep` call. The earlier structural-copy measurements remain separate because their alias contract differs. Each snapshot loads its own ABI-matched extension; all absent newer entrypoints are explicitly disabled so current kernels cannot leak into the control.

## What changed

The existing per-call pointer memo used a cheap pointer hash with linear probing. Structured allocator addresses could form long clusters; the large-record workload exposed this despite ordinary small graphs appearing fast. The final table keeps the cheap hash and uses perturbation probing, mixing higher hash bits into the probe sequence. Strong source/result references, private completed entries and all size/depth limits remain in place. The separate [exploratory hash comparison](clone-common-hash-probes-py313.json), captured before the final shared-loop specialization, compares the public APIs with a stronger avalanche hash plus linear probing and with cheap hash plus perturbation probing. The cheaper perturbation candidate was generally faster on common small graphs; these exploratory samples are not substituted for the final paired results. Table shape and allocator layout still affect the measured gain.

The final [offline hybrid-probing control](clone-common-hybrid-probes-py313.json) tested linear probing for small tables and perturbation probing above capacity cutoffs of 64, 256 and 2048, using nine rounds through the same public wrappers. The 2048 cutoff improved ordinary nested records, flat records, wide lists and dataclasses by only 1–4%; repeated aliases improved 11%, while the large-record case slowed 0.6%. Smaller cutoffs showed no broad gain. This missed the required 5% improvement across common small cases, so the production algorithm stays unchanged. These candidate measurements remain separate from the final paired runs.

The new native continuation traverses supported containers and admitted dataclass instances with the actual Python memo, registering initialized mutable prefixes and record twins before their children. Foreign nodes return to the selected Python handler with that same memo. Class admission occurs at the current owned node, with a per-call type cache, preserving constructor, descriptor and copy-hook ordering. This reduces repeated Python dispatch across ordinary record collections without replaying fallback reconstruction.

The closed builtin traversal keeps separate compact memo and sequence/dictionary entrypoints. Shared loop bodies specialize their direct-memo mode at compile time. Primitive atom checks stay on the hot closed loop; rarer graph atoms return by identity at the generic continuation boundary before identity/memo/frame work. Dictionary cursors keep live mutation checks and owned key/value references; incomplete private storage stays hidden from GC observers. Callback-visible mutable results expose initialized prefixes.

`deepcopy` remains stdlib's function. `deep` preserves aliases, exact builtin container/record types and atomic identities; unsupported protocols and native-refused cycles retain their shared-memo fallback. `asdict` keeps field conversion and repeated structural expansion. There is no serialization shortcut in the production runtime.

## Measurement

Inputs and correctness references are prepared outside measurement. Exact nested types, dictionary order/key types, dataclass fields/state, mutable isolation, alias preservation and builtin atomic identity are checked before timing. Known output-node counts distinguish graph copying from `asdict` expansion; foreign protocol result nodes are excluded.

Untimed validation and calibration warm admission and field caches, representing repeated public calls. Batches are calibrated to at least 5 ms, repeated nine times, with implementation order reversed on alternate rounds. `process_time_ns()` and `perf_counter_ns()` are recorded separately; tables show median batch means. Profiles run separately and do not supply timing samples. GC-on and GC-off runs use the same stable interpreter environment and matching release flags for before/current. Raw JSON retains every sample, source/binary/entrypoint hashes, Python build, GC thresholds and harness hashes.

## CPython 3.13, GC disabled

Times are microseconds per public operation. Ratios below 1× indicate slowdowns. `asdict` rows use `dataclasses.asdict` as stdlib control; other rows use `copy.deepcopy`.

| Workload | Before CPU | Current CPU | Gain vs before | Gain vs stdlib | Current wall | Wall gain vs before |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.078 | 0.078 | 1.00× | 11.04× | 0.078 | 1.00× |
| nested-200 | 19.697 | 22.271 | 0.88× | 8.66× | 22.575 | 0.87× |
| records-500 | 25.859 | 27.379 | 0.94× | 13.70× | 27.535 | 0.94× |
| tuple-records-500 | 34.188 | 38.402 | 0.89× | 13.43× | 38.886 | 0.89× |
| wide-lists-1000 | 32.531 | 37.289 | 0.87× | 8.75× | 37.468 | 0.87× |
| deep-80 | 4.138 | 4.188 | 0.99× | 4.51× | 4.189 | 0.99× |
| large-records-20000 | 28557.000 | 1253.667 | 22.78× | 9.59× | 1254.445 | 22.79× |
| repeated-aliases-500 | 2.511 | 2.611 | 0.96× | 11.32× | 2.612 | 0.96× |
| dataclasses-200 | 140.274 | 64.984 | 2.16× | 5.77× | 65.018 | 2.16× |
| dataclass-records-500 | 91.827 | 91.622 | 1.00× | 5.31× | 91.758 | 1.00× |
| mixed-keys-1000 | 220.350 | 86.979 | 2.53× | 5.67× | 87.020 | 2.53× |
| foreign-last | 18.583 | 6.528 | 2.85× | 13.01× | 6.538 | 2.85× |
| asdict-records-500 | 46.586 | 46.031 | 1.01× | 3.96× | 46.090 | 1.01× |
| nested-small-5 | 0.525 | 0.549 | 0.96× | 9.53× | 0.550 | 0.96× |
| nested-wide-1000 | 94.050 | 108.417 | 0.87× | 8.51× | 108.455 | 0.87× |
| balanced-dict-list | 10.018 | 10.701 | 0.94× | 9.90× | 10.700 | 0.94× |
| dictionary-of-records-500 | 320.533 | 96.439 | 3.32× | 8.95× | 96.535 | 3.32× |
| dataclass-small | 0.773 | 0.718 | 1.08× | 2.65× | 0.718 | 1.08× |
| dataclasses-copy-hooks-40 | 50.766 | 21.586 | 2.35× | 3.01× | 21.585 | 2.35× |
| dataclasses-frozen-200 | 140.766 | 65.914 | 2.14× | 5.57× | 65.912 | 2.14× |
| dataclasses-slots-200 | 570.688 | 523.111 | 1.09× | 0.97× | 523.391 | 1.09× |
| dataclasses-flat-500 | 297.656 | 128.472 | 2.32× | 6.31× | 128.572 | 2.32× |
| dataclasses-datetime-200 | 261.750 | 125.412 | 2.09× | 4.16× | 125.524 | 2.09× |
| dataclass-tree-200 | 141.897 | 66.695 | 2.13× | 5.61× | 66.687 | 2.13× |
| asdict-tree-200 | 49.633 | 49.961 | 0.99× | 1.93× | 49.958 | 0.99× |

## CPython 3.13, GC enabled

Times are microseconds per public operation. Ratios below 1× indicate slowdowns. `asdict` rows use `dataclasses.asdict` as stdlib control; other rows use `copy.deepcopy`.

| Workload | Before CPU | Current CPU | Gain vs before | Gain vs stdlib | Current wall | Wall gain vs before |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.078 | 0.084 | 0.92× | 10.20× | 0.084 | 0.92× |
| nested-200 | 18.756 | 20.258 | 0.93× | 9.38× | 20.456 | 0.92× |
| records-500 | 28.000 | 28.668 | 0.98× | 14.51× | 28.926 | 0.97× |
| tuple-records-500 | 33.715 | 37.203 | 0.91× | 14.13× | 37.549 | 0.90× |
| wide-lists-1000 | 33.719 | 39.617 | 0.85× | 8.79× | 39.824 | 0.86× |
| deep-80 | 4.731 | 4.910 | 0.96× | 4.87× | 5.140 | 0.94× |
| large-records-20000 | 26585.000 | 1238.833 | 21.46× | 9.74× | 1239.507 | 21.45× |
| repeated-aliases-500 | 2.332 | 2.518 | 0.93× | 11.42× | 2.520 | 0.93× |
| dataclasses-200 | 139.694 | 64.734 | 2.16× | 5.70× | 64.726 | 2.16× |
| dataclass-records-500 | 88.856 | 89.255 | 1.00× | 5.30× | 89.371 | 1.00× |
| mixed-keys-1000 | 220.200 | 87.330 | 2.52× | 5.77× | 87.333 | 2.52× |
| foreign-last | 18.543 | 6.542 | 2.83× | 13.06× | 6.545 | 2.83× |
| asdict-records-500 | 47.898 | 48.234 | 0.99× | 3.99× | 48.229 | 1.00× |
| nested-small-5 | 0.535 | 0.572 | 0.94× | 9.28× | 0.572 | 0.94× |
| nested-wide-1000 | 259.778 | 112.237 | 2.31× | 8.63× | 112.219 | 2.31× |
| balanced-dict-list | 10.503 | 11.031 | 0.95× | 10.08× | 11.056 | 0.96× |
| dictionary-of-records-500 | 333.250 | 99.333 | 3.35× | 9.32× | 99.443 | 3.36× |
| dataclass-small | 0.817 | 0.759 | 1.08× | 2.70× | 0.769 | 1.06× |
| dataclasses-copy-hooks-40 | 54.227 | 22.581 | 2.40× | 3.05× | 22.761 | 2.39× |
| dataclasses-frozen-200 | 158.138 | 72.136 | 2.19× | 5.66× | 72.715 | 2.20× |
| dataclasses-slots-200 | 671.312 | 592.000 | 1.13× | 1.00× | 604.805 | 1.13× |
| dataclasses-flat-500 | 307.500 | 137.400 | 2.24× | 6.31× | 137.414 | 2.24× |
| dataclasses-datetime-200 | 271.118 | 123.721 | 2.19× | 4.35× | 123.778 | 2.19× |
| dataclass-tree-200 | 154.591 | 71.787 | 2.15× | 5.77× | 73.021 | 2.15× |
| asdict-tree-200 | 51.789 | 52.461 | 0.99× | 1.89× | 52.488 | 0.99× |

## CPython 3.14, GC disabled

Times are microseconds per public operation. Ratios below 1× indicate slowdowns. `asdict` rows use `dataclasses.asdict` as stdlib control; other rows use `copy.deepcopy`.

| Workload | Before CPU | Current CPU | Gain vs before | Gain vs stdlib | Current wall | Wall gain vs before |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.076 | 0.076 | 1.00× | 5.87× | 0.076 | 1.00× |
| nested-200 | 19.512 | 21.815 | 0.89× | 5.62× | 21.820 | 0.89× |
| records-500 | 25.699 | 27.387 | 0.94× | 7.22× | 27.400 | 0.94× |
| tuple-records-500 | 34.496 | 37.812 | 0.91× | 8.45× | 37.826 | 0.91× |
| wide-lists-1000 | 35.062 | 38.305 | 0.92× | 6.24× | 38.309 | 0.92× |
| deep-80 | 4.234 | 4.311 | 0.98× | 4.18× | 4.310 | 0.98× |
| large-records-20000 | 19125.000 | 1386.000 | 13.80× | 5.30× | 1413.396 | 13.80× |
| repeated-aliases-500 | 2.496 | 2.713 | 0.92× | 11.04× | 2.725 | 0.92× |
| dataclasses-200 | 131.456 | 65.492 | 2.01× | 4.36× | 65.585 | 2.01× |
| dataclass-records-500 | 89.000 | 90.337 | 0.99× | 3.40× | 90.330 | 0.99× |
| mixed-keys-1000 | 202.370 | 85.854 | 2.36× | 3.44× | 86.019 | 2.35× |
| foreign-last | 17.572 | 6.654 | 2.64× | 3.99× | 6.656 | 2.64× |
| asdict-records-500 | 46.586 | 46.375 | 1.00× | 3.73× | 46.402 | 1.00× |
| nested-small-5 | 0.530 | 0.547 | 0.97× | 6.49× | 0.547 | 0.97× |
| nested-wide-1000 | 93.294 | 105.671 | 0.88× | 5.68× | 105.668 | 0.88× |
| balanced-dict-list | 10.127 | 11.031 | 0.92× | 6.28× | 11.036 | 0.92× |
| dictionary-of-records-500 | 174.679 | 95.686 | 1.83× | 5.59× | 95.712 | 1.83× |
| dataclass-small | 0.653 | 0.693 | 0.94× | 2.01× | 0.693 | 0.94× |
| dataclasses-copy-hooks-40 | 49.266 | 22.010 | 2.24× | 2.62× | 22.202 | 2.22× |
| dataclasses-frozen-200 | 132.621 | 66.281 | 2.00× | 4.31× | 66.291 | 2.00× |
| dataclasses-slots-200 | 444.864 | 409.773 | 1.09× | 0.96× | 409.786 | 1.09× |
| dataclasses-flat-500 | 287.688 | 134.243 | 2.14× | 4.93× | 135.338 | 2.14× |
| dataclasses-datetime-200 | 254.306 | 121.842 | 2.09× | 3.59× | 121.902 | 2.09× |
| dataclass-tree-200 | 160.258 | 78.820 | 2.03× | 4.67× | 90.966 | 2.01× |
| asdict-tree-200 | 49.031 | 57.117 | 0.86× | 1.84× | 62.414 | 1.06× |

## CPython 3.14, GC enabled

Times are microseconds per public operation. Ratios below 1× indicate slowdowns. `asdict` rows use `dataclasses.asdict` as stdlib control; other rows use `copy.deepcopy`.

| Workload | Before CPU | Current CPU | Gain vs before | Gain vs stdlib | Current wall | Wall gain vs before |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.077 | 0.077 | 0.99× | 5.75× | 0.077 | 0.99× |
| nested-200 | 20.430 | 21.997 | 0.93× | 5.78× | 21.997 | 0.93× |
| records-500 | 26.133 | 27.918 | 0.94× | 7.19× | 27.915 | 0.94× |
| tuple-records-500 | 44.008 | 46.945 | 0.94× | 8.91× | 55.712 | 0.89× |
| wide-lists-1000 | 34.125 | 40.305 | 0.85× | 6.19× | 40.831 | 0.84× |
| deep-80 | 4.190 | 4.315 | 0.97× | 4.22× | 4.318 | 0.97× |
| large-records-20000 | 29134.000 | 1565.000 | 18.62× | 4.74× | 1564.823 | 18.62× |
| repeated-aliases-500 | 2.642 | 2.953 | 0.89× | 10.56× | 2.978 | 0.90× |
| dataclasses-200 | 134.891 | 67.281 | 2.00× | 4.46× | 67.341 | 2.01× |
| dataclass-records-500 | 86.532 | 91.761 | 0.94× | 3.49× | 91.757 | 0.94× |
| mixed-keys-1000 | 204.696 | 88.890 | 2.30× | 3.41× | 89.035 | 2.30× |
| foreign-last | 17.885 | 6.759 | 2.65× | 4.04× | 6.777 | 2.65× |
| asdict-records-500 | 50.438 | 49.402 | 1.02× | 3.77× | 49.396 | 1.02× |
| nested-small-5 | 0.594 | 0.582 | 1.02× | 7.04× | 0.589 | 1.04× |
| nested-wide-1000 | 101.652 | 114.561 | 0.89× | 5.60× | 114.780 | 0.89× |
| balanced-dict-list | 10.442 | 11.059 | 0.94× | 6.37× | 11.057 | 0.95× |
| dictionary-of-records-500 | 177.077 | 94.538 | 1.87× | 5.72× | 95.258 | 1.86× |
| dataclass-small | 0.674 | 0.700 | 0.96× | 2.08× | 0.700 | 0.96× |
| dataclasses-copy-hooks-40 | 46.016 | 20.784 | 2.21× | 2.62× | 20.798 | 2.21× |
| dataclasses-frozen-200 | 136.196 | 68.266 | 2.00× | 4.36× | 68.566 | 1.99× |
| dataclasses-slots-200 | 642.545 | 569.727 | 1.13× | 0.99× | 576.566 | 1.15× |
| dataclasses-flat-500 | 299.364 | 141.565 | 2.11× | 4.43× | 141.986 | 2.13× |
| dataclasses-datetime-200 | 243.658 | 117.750 | 2.07× | 3.66× | 117.740 | 2.07× |
| dataclass-tree-200 | 131.314 | 66.492 | 1.97× | 4.33× | 66.491 | 1.98× |
| asdict-tree-200 | 46.609 | 46.516 | 1.00× | 2.03× | 46.549 | 1.00× |

## Repository default Python 3.14.9

The repository’s `.venv/bin/python` was also measured directly, with GC enabled and nine alternating rounds. Its ordinary import resolves to this main checkout and the actual new native continuation; the same ABI-matched release extension pair is used for both implementations. This additional run includes the public wrappers and output destruction, with every input/result checked as above. [Raw default-environment samples](clone-common-cpu-default-py314.json).

| Workload | Before CPU, µs | Current CPU, µs | Gain vs before | Gain vs stdlib |
| --- | ---: | ---: | ---: | ---: |
| nested-200 | 19.891 | 21.133 | 0.94× | 5.89× |
| wide-lists-1000 | 35.617 | 37.719 | 0.94× | 6.77× |
| dataclasses-200 | 142.712 | 71.267 | 2.00× | 4.25× |
| dataclasses-flat-500 | 405.812 | 169.735 | 2.39× | 5.00× |
| large-records-20000 | 28232.000 | 1539.500 | 18.34× | 4.66× |

Reproduce this additional run from the repository root with its ordinary interpreter:

```sh
.venv/bin/python -m bench.profiles.clone_common_cpu --compare-dir /tmp/clone-main-before --rounds 9 --gc-enabled --output /tmp/common-default.json
```

## Profiles and remaining costs

| Three public copies | Before Python `_walk` calls | Current Python `_walk` calls | Current native continuation calls |
| --- | ---: | ---: | ---: |
| nested-200 | 0 | 0 | 0 |
| dataclasses-200 | 603 | 0 | 3 |
| dataclasses-flat-500 | 1503 | 0 | 3 |
| dataclass-tree-200 | 609 | 0 | 3 |
| asdict-records-500 | 0 | 0 | 0 |

The tables show the limits of this change. Wide independent lists, tiny graphs and repeated aliases can favor the previous closed kernel; additional container allocation and private-table work remain visible. Slot dataclasses retain protocol fallback and measure 1.09–1.13× against before. Copy-hook fields improve 2.21–2.40× while still running their hooks. The large-table improvement is sensitive to allocator address distribution and should not be generalized to all dictionaries. These local results are measurements, not guarantees for arbitrary graphs or platforms.

## Cold class-admission latency

The separate diagnostic clears `_NODES`, `_RECORDS` and `_FIELDS` outside each timer, then measures one public operation plus output destruction for 101 repetitions. Imports, interpreter, allocator and prepared inputs remain warm; this is not process startup. Single-operation CPU readings can be quantized on this platform, so wall latency and an empty-call timer control are retained.

| Interpreter / workload | Before wall, µs | Current wall, µs | Wall gain | Current CPU, µs |
| --- | ---: | ---: | ---: | ---: |
| 3.13 / nested-200 | 18.666 | 21.042 | 0.89× | 22.000 |
| 3.13 / dataclasses-200 | 138.458 | 65.375 | 2.12× | 66.000 |
| 3.13 / nested-small-5 | 0.708 | 0.708 | 1.00× | 1.000 |
| 3.13 / dataclass-small | 2.083 | 2.041 | 1.02× | 2.000 |
| 3.13 / dataclasses-flat-500 | 300.500 | 130.375 | 2.30× | 131.000 |
| 3.13 / asdict-tree-200 | 50.500 | 49.833 | 1.01× | 50.000 |
| 3.13 / empty-call timer | — | 0.083 | — | 1.000 |
| 3.14 / nested-200 | 21.041 | 21.500 | 0.98× | 22.000 |
| 3.14 / dataclasses-200 | 133.416 | 66.792 | 2.00× | 67.000 |
| 3.14 / nested-small-5 | 0.709 | 0.750 | 0.95× | 1.000 |
| 3.14 / dataclass-small | 1.917 | 1.958 | 0.98× | 2.000 |
| 3.14 / dataclasses-flat-500 | 285.958 | 135.083 | 2.12× | 136.000 |
| 3.14 / asdict-tree-200 | 47.334 | 46.833 | 1.01× | 47.000 |
| 3.14 / empty-call timer | — | 0.083 | — | 0.000 |

## Alternative algorithms and allocation reference

The [algorithm diagnostic](../../diagnostics/clone_algorithms.py) measures complete encode/decode roundtrips and fixed-shape Python copiers beside public clone calls. Failed strict contract checks remain visible and never count as valid wins. JSON can erase record/tuple types, serialization can replace immutable atom identities, and pickle follows reduction protocols rather than arbitrary deepcopy hooks. A candidate passing these prepared inputs is not proof of general protocol equivalence. Fixed-shape Python copiers skip arbitrary-shape admission and are controls, not general replacements.

| Workload | Public CPU, µs | Specialized Python CPU, µs | Pickle5 CPU / valid | Marshal2 CPU / valid | orjson CPU / valid |
| --- | ---: | ---: | ---: | ---: | ---: |
| nested-200 | 20.742 | 11.598 | 29.602 / no | 36.141 / no | 18.307 / no |
| records-500 | 26.625 | 17.730 | 57.155 / no | 103.744 / no | 62.000 / no |
| tuple-records-500 | 36.137 | — | 40.966 / no | 61.109 / no | 43.430 / no |
| dataclasses-200 | 65.097 | 25.201 | 79.023 / no | unsupported | 21.424 / no |
| dataclass-records-500 | 90.872 | — | 75.750 / no | unsupported | 48.445 / no |
| asdict-records-500 | 48.031 | — | 76.154 / no | unsupported | 48.367 / no |

The separately compiled [empty-node control](../../diagnostics/clone_alloc_floor.c) allocates only empty lists, dictionaries and ordinary record instances. It excludes traversal, payload references, populated hash tables and protocol work. This is a favorable allocation reference, **not a formal lower bound or universal speedup ceiling**: pools, layout, algorithm and context affect costs. Hundreds of independent Python outputs remain material CPU work even after traversal improves.

| Workload | Lists / dicts / records | Empty-node CPU, µs | Public CPU, µs | Stdlib CPU, µs |
| --- | ---: | ---: | ---: | ---: |
| nested-200 | 201 / 201 / 0 | 3.524 | 21.419 | 122.970 |
| records-500 | 1 / 500 / 0 | 5.032 | 27.812 | 196.023 |
| dataclasses-200 | 201 / 200 / 200 | 7.235 | 67.836 | 290.067 |
| dataclass-records-500 | 501 / 501 / 1 | 9.837 | 89.440 | 306.143 |
| asdict-records-500 | 501 / 501 / 0 | 9.699 | 46.406 | 175.717 |

## Evidence and reproduction

Public paired runs: [CPython 3.13, GC disabled](clone-common-cpu-py313.json), [CPython 3.13, GC enabled](clone-common-cpu-py313-gc.json), [CPython 3.14, GC disabled](clone-common-cpu-py314.json), [CPython 3.14, GC enabled](clone-common-cpu-py314-gc.json). Cold-cache diagnostics: [3.13](clone-common-cold-py313.json), [3.14](clone-common-cold-py314.json). Separate [algorithm probes](clone-common-algorithms-py314.json) and [allocation control](clone-common-allocations-py314.json) are not substituted for public performance. Memory and callback validation is recorded in [the safety report](clone-memory-safety.md).

Reconstruct the exact baseline from committed sources in a scratch snapshot directory, without altering the checkout. The manifest must pin `clone_checked`, `clone_graph`, `clone_dataclass` and legacy `clone_records`, set `copy_contract` to `shared`, and omit new walker/record-graph bindings:

```sh
mkdir -p /tmp/clone-main-before
git show 31deedf072af28d0cd535801d79da5974321543a:src/unstd/clone/structural.py > /tmp/clone-main-before/structural.py
git show 31deedf072af28d0cd535801d79da5974321543a:native/clone/src/adapter.c > /tmp/clone-main-before/adapter.c
```

Build baseline and current extensions with the same selected interpreter and release flags. This reproduces the measured `sysconfig` release command; do not reuse a binary from another Python ABI. Run this block and the measurement commands separately with CPython 3.13 and 3.14:

```sh
python - <<'PY'
import json
from pathlib import Path
import shlex
import subprocess
import sysconfig
snapshot = Path('/tmp/clone-main-before')
(snapshot / 'manifest.json').write_text(json.dumps({
    'native_entrypoint': 'clone_checked',
    'native_graph_entrypoint': 'clone_graph',
    'native_dataclass_entrypoint': 'clone_dataclass',
    'native_records_entrypoint': 'clone_records',
    'copy_contract': 'shared',
}))
for source, directory in (
    (snapshot / 'adapter.c', snapshot),
    (Path('native/clone/src/adapter.c'), Path('src/unstd/clone')),
):
    target = directory / ('_clone_native' + sysconfig.get_config_var('EXT_SUFFIX'))
    staged = target.with_name(target.name + '.staged')
    subprocess.run([
        *shlex.split(sysconfig.get_config_var('LDSHARED')),
        *shlex.split(sysconfig.get_config_var('CFLAGS')),
        '-fPIC', '-I' + sysconfig.get_path('include'), str(source), '-o', str(staged),
    ], check=True)
    staged.replace(target)
PY
```

```sh
PYTHONPATH=src python -m bench.profiles.clone_common_cpu --compare-dir /tmp/clone-main-before --rounds 9 --output /tmp/common.json
PYTHONPATH=src python -m bench.profiles.clone_common_cpu --compare-dir /tmp/clone-main-before --rounds 9 --gc-enabled --output /tmp/common-gc.json
PYTHONPATH=src python -m bench.diagnostics.clone_cold --compare-dir /tmp/clone-main-before --rounds 101 --output /tmp/cold.json
PYTHONPATH=src python -m bench.diagnostics.clone_algorithms --compare-dir /tmp/clone-main-before --rounds 9 --output /tmp/algorithms.json
```

Compile `bench/diagnostics/clone_alloc_floor.c` separately with the same interpreter's extension flags, naming the output `_clone_alloc_floor<EXT_SUFFIX>` in a scratch directory. It has no production dependency. Then run:

```sh
PYTHONPATH=src python -m bench.diagnostics.clone_allocations --module-path /absolute/path/to/_clone_alloc_floor.EXT_SUFFIX --compare-dir /tmp/clone-main-before --rounds 9 --output /tmp/allocations.json
```

| Measured source | SHA-256 |
| --- | --- |
| `src/unstd/clone/structural.py` | `958c02654a63434fd3bdd2cea6a49ae79c64abee05f4f2761f2902257f210942` |
| `native/clone/src/adapter.c` | `46f14a52dc14c1271683e4a5daf6d9a37ae138409a9b1d8c1dc1521851e24793` |
| `bench/profiles/clone_common_cpu.py` | `daa59b900df3325911fcfe1aa96ee9ebff6dc80d3e54a9a2387ce98bf190c46d` |
| `bench/profiles/clone_cpu.py` | `ee6c8968965fef626b090479eeaccf362ec9a0ff46b81a7972b585dcb7b12767` |

Baseline wrapper SHA-256: `28016659924a5bfdfefd8a5199aad43697afb4301f0683dd6fcd91d3d258fc3c`; C SHA-256: `e1c881d6842a45880a4a70aa02a0c57fae2982eb4f4e6a303da28f1af7d76369`. Each raw report records matching ABI binary hashes and all selected native entrypoints.
