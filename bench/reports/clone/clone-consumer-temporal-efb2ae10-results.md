# Consumer and temporal clone iteration: first frozen candidate

We measured all thirty common cases, thirteen consumer cases and fourteen optional temporal cases through public Python APIs. Eight of 57 cases exceed 100× stdlib on both matched CPU and wall clocks. All ten homogeneous temporal lists exceed 100× on CPU; three miss the wall target. All thirteen consumer cases remain below 100×.

The 1.10 before/after regression gate fails. Three cases lose on CPU: `dataclass-small` 1.1247×, `dataclasses-datetime-200` 1.4078× and `consumer-copy-hook-alias` 1.1081×. Fourteen wall metrics also fail. We retain every result and make no completed regression-qualification claim.

## Method and contract

This local arm64 run uses CPython 3.14.9, Pydantic 2.13.5 and the same dependencies for current, before, installed 4.0.2 and stdlib. Seven alternating-order rounds include normal GC and output destruction. Inputs, admission caches and field caches are prepared before timing; calibration uses the fastest of three probes to choose roughly 50ms batches. Reported speedups are the median of matched round ratios, while CPU times are separate medians.

The retained load receipt records a one-minute load average of 136.6 across eighteen logical CPUs during measurement. Our other qualification jobs had finished. This constrains wall-time inference; every wall sample and gate failure remains in the report.

Current and before must retain exact immutable temporal source identities. Stdlib may rebuild equal values; this existing unstd sharing contract makes those allocations unnecessary. Checks still require exact temporal kinds, timezone identity, fold values, fresh structural containers and unchanged output order. Consumer checks preserve aliases, cycles, private state and foreign copy-hook semantics. Both optional sets leave the original thirty-case set unchanged.

The public wiring receipt proves successful native builtin, dataclass, asdict, fused-model and closed-temporal routes. The fused proof restores the actual canonical C binding and exact integer capability before observing C calls/returns and rejecting Python `_model` frames. The temporal proof retains public call arguments and requires successful graph output with zero mixed or Python walking. All 57 outputs pass their untimed checks before measurement.

## Intentional improvements

We fuse canonical model allocation and state copying into the native shared-memo walk. Small private-state models retain more Python work than scalar model batches. Frozen slotted records keep their normal reducer/state protocol while their payloads use the native walker; the more aggressive slot-allocation prototype was rejected by descriptor tests.

Complete flat sequences of exact safe temporal values now validate after output allocation and share immutable leaves in one native call without per-leaf memo entries. The canonical cycle path can continue through the shared memo after a closed-walk refusal. Foreign or changed walkers retain their earlier fallback behavior.

## Temporal results

| Temporal | Current CPU µs | vs before CPU | vs stdlib CPU | vs stdlib wall | 100× both clocks |
| --- | ---: | ---: | ---: | ---: | --- |
| `date-1000` | 7.710 | 25.27× | 114.88× | 107.95× | met |
| `date-10000` | 64.730 | 27.08× | 135.24× | 134.64× | met |
| `datetime-naive-1000` | 7.449 | 17.17× | 122.79× | 210.34× | met |
| `datetime-naive-10000` | 69.517 | 17.74× | 125.40× | 116.53× | met |
| `datetime-utc-1000` | 7.648 | 22.35× | 124.30× | 213.72× | met |
| `datetime-utc-10000` | 73.078 | 24.00× | 135.27× | 69.53× | miss |
| `time-naive-1000` | 7.362 | 15.36× | 111.05× | 91.33× | miss |
| `time-naive-10000` | 71.112 | 16.02× | 115.90× | 91.05× | miss |
| `time-utc-1000` | 7.794 | 22.21× | 123.68× | 131.69× | met |
| `time-utc-10000` | 75.648 | 22.79× | 126.81× | 114.35× | met |
| `mixed-scalars-1000` | 5.954 | 7.89× | 42.39× | 23.32× | miss |
| `tuple-date-1000` | 6.185 | 26.48× | 136.35× | 119.64× | met |
| `nested-date-1000` | 289.125 | 1.01× | 4.28× | 4.51× | miss |
| `scalar-first-1000` | 161.887 | 0.99× | 5.02× | 5.21× | miss |

Date 1k/10k, naive datetime 1k/10k, UTC datetime 1k, UTC time 1k/10k and tuple date 1k meet both clocks. UTC datetime 10k and naive time 1k/10k exceed 100× CPU but miss 100× wall in this run. The mixed-scalar, nested and scalar-first controls remain below the target.

## Consumer results

| Consumer | Current CPU µs | vs before CPU | vs before wall | vs stdlib CPU | vs stdlib wall | 100× CPU target ns | Further CPU needed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `question-tiny` | 2.799 | 1.09× | 4.39× | 1.36× | 4.91× | 36.2 | 73.5× |
| `question-nested-100` | 31.251 | 1.05× | 1.45× | 3.39× | 4.99× | 1059.6 | 29.5× |
| `question-private-alias` | 3.584 | 1.06× | 0.99× | 1.49× | 1.44× | 55.2 | 67.0× |
| `attachment-empty` | 1.491 | 1.20× | 1.14× | 1.68× | 1.81× | 25.2 | 59.5× |
| `attachment-images-10` | 10.972 | 1.57× | 1.77× | 1.75× | 2.14× | 192.5 | 57.0× |
| `materials-scalar-40` | 40.801 | 1.60× | 1.94× | 1.83× | 1.70× | 751.7 | 54.6× |
| `materials-data-40` | 153.620 | 1.21× | 0.92× | 3.05× | 3.18× | 4673.1 | 32.8× |
| `change-slots-tiny` | 2.468 | 1.44× | 2.04× | 1.33× | 1.62× | 32.8 | 75.0× |
| `change-slots-1000` | 3656.000 | 1.40× | 1.40× | 1.42× | 2.38× | 49732.7 | 70.5× |
| `dataclass-field-alias` | 1.009 | 0.92× | 0.95× | 3.33× | 2.96× | 33.2 | 30.1× |
| `asdict-field-alias` | 0.871 | 0.98× | 0.81× | 2.03× | 1.84× | 18.3 | 49.4× |
| `builtin-cycle` | 0.937 | 1.63× | 0.84× | 1.27× | 1.13× | 11.9 | 78.5× |
| `copy-hook-alias` | 1.136 | 0.90× | 0.70× | 1.12× | 0.78× | 12.7 | 89.3× |

All thirteen miss 100×. Images and scalar materials improve by about 1.6× over before on CPU; slots 1000 improves by 1.4×. The cycle case improves by 1.63× CPU after native continuation, while its wall result loses. Dataclass alias, asdict alias and copy-hook alias remain CPU losses against before. The copy-hook case also loses to stdlib on wall time.

## Original common results

| Common case | Current CPU µs | vs before CPU | vs before wall | vs stdlib CPU | vs stdlib wall |
| --- | ---: | ---: | ---: | ---: | ---: |
| `small-dict` | 0.147 | 0.92× | 0.97× | 5.05× | 4.17× |
| `nested-200` | 34.875 | 1.01× | 1.45× | 5.89× | 7.47× |
| `records-500` | 45.796 | 1.00× | 0.79× | 7.17× | 5.35× |
| `tuple-records-500` | 59.599 | 0.99× | 0.71× | 9.20× | 11.69× |
| `wide-lists-1000` | 63.482 | 0.98× | 0.79× | 6.64× | 5.38× |
| `deep-80` | 5.584 | 1.01× | 1.88× | 5.34× | 9.72× |
| `large-records-20000` | 3163.167 | 1.01× | 1.05× | 4.83× | 4.03× |
| `repeated-aliases-500` | 1.393 | 0.99× | 0.70× | 35.50× | 15.86× |
| `dataclasses-200` | 100.257 | 0.97× | 0.60× | 5.02× | 6.04× |
| `dataclass-records-500` | 142.330 | 0.98× | 0.76× | 3.84× | 4.05× |
| `mixed-keys-1000` | 153.719 | 1.01× | 1.15× | 3.47× | 3.51× |
| `foreign-last` | 11.737 | 0.96× | 0.84× | 3.72× | 2.20× |
| `asdict-records-500` | 76.478 | 0.99× | 0.70× | 3.95× | 2.64× |
| `nested-small-5` | 0.803 | 0.98× | 1.40× | 6.77× | 11.01× |
| `nested-wide-1000` | 167.531 | 1.00× | 1.18× | 6.09× | 7.82× |
| `balanced-dict-list` | 17.263 | 1.00× | 0.68× | 6.08× | 5.63× |
| `dictionary-of-records-500` | 152.781 | 0.98× | 0.87× | 5.89× | 3.52× |
| `dataclass-small` | 0.697 | 0.89× | 1.18× | 3.35× | 3.99× |
| `dataclasses-copy-hooks-40` | 32.328 | 1.00× | 1.01× | 2.81× | 7.00× |
| `dataclasses-frozen-200` | 99.971 | 1.00× | 0.93× | 4.53× | 6.74× |
| `dataclasses-slots-200` | 335.734 | 2.03× | 1.97× | 2.00× | 2.43× |
| `dataclasses-flat-500` | 196.246 | 1.03× | 1.49× | 5.09× | 7.52× |
| `dataclasses-datetime-200` | 196.953 | 0.71× | 1.03× | 3.66× | 4.16× |
| `dataclass-tree-200` | 94.484 | 1.02× | 1.05× | 5.07× | 5.52× |
| `asdict-tree-200` | 70.536 | 1.02× | 4.19× | 2.07× | 4.55× |
| `scalar-list-1000` | 4.020 | 0.99× | 0.98× | 11.36× | 17.83× |
| `scalar-tuple-1000` | 3.939 | 0.99× | 0.57× | 14.23× | 12.06× |
| `scalar-list-100000` | 369.359 | 1.00× | 1.00× | 10.09× | 11.50× |
| `repeated-aliases-10000` | 23.917 | 1.00× | 1.12× | 36.23× | 30.84× |
| `repeated-aliases-100000` | 234.258 | 0.97× | 1.30× | 36.55× | 41.05× |

## Regression failures

The gate retains these seventeen failing metrics. The mixed datetime CPU loss is material: the new safety guard scans the 73-entry globals namespace and two policy tables at each datetime/time leaf. The small dataclass and copy-hook cases also retain fixed dispatch/guard overhead. Wall scheduling is visibly noisy, including cases whose CPU result improves; we do not remove or waive their failures.

| Case | Metric | Matched slowdown |
| --- | --- | ---: |
| `records-500` | wall | 1.2638× |
| `tuple-records-500` | wall | 1.4005× |
| `wide-lists-1000` | wall | 1.2599× |
| `repeated-aliases-500` | wall | 1.4328× |
| `dataclasses-200` | wall | 1.6610× |
| `dataclass-records-500` | wall | 1.3151× |
| `foreign-last` | wall | 1.1848× |
| `asdict-records-500` | wall | 1.4328× |
| `balanced-dict-list` | wall | 1.4715× |
| `dictionary-of-records-500` | wall | 1.1518× |
| `dataclass-small` | cpu | 1.1247× |
| `dataclasses-datetime-200` | cpu | 1.4078× |
| `scalar-tuple-1000` | wall | 1.7697× |
| `consumer-asdict-field-alias` | wall | 1.2371× |
| `consumer-builtin-cycle` | wall | 1.1927× |
| `consumer-copy-hook-alias` | wall | 1.4362× |
| `consumer-copy-hook-alias` | cpu | 1.1081× |

## Qualification and ownership

Normal unit runs pass on Python 3.14 (615 passed, 42 skipped), Python 3.12 (617 passed, 40 skipped) and Python 3.13 (617 passed, 40 skipped). The separate owned-only C build passes 169 tests with 32 skipped. The documented Python source differs from that qualification only in its module docstring; the saved AST receipt verifies executable code and source locations are identical.

Completed ASan/UBSan forced-GC selection covers 136 tests with one skip on the pre-cycle-guard source, whose selected functions are unchanged by the cycle patch. Four final cycle tests also pass. A broader final forced-GC run was interrupted after over six minutes in an existing large memo-export stress. Its partial output and native stack sample are retained. We do not claim that broader run completed.

The measured current C source is `efb2ae10…`; before and current both retain the same pre-existing unrelated callable safety delta. The archive also saves owned-only C `750d56ed…`, which excludes that delta and has separate correctness evidence. These numeric measurements apply to the measured working source; the owned-only build was not separately timed. Installed4.0.2's actual binary is a secondary control with matching interpreter/dependencies, but its matching C source and compiler settings were unavailable.

## Evidence and reproduction

[Raw rounds](clone-consumer-temporal-efb2ae10-py314-gc.json), [receipt](clone-consumer-temporal-efb2ae10-receipt.json), [sources](clone-consumer-temporal-efb2ae10-sources.tar.gz) and [qualification](clone-consumer-temporal-efb2ae10-qualification.tar.gz) retain hashes, all 49 target gaps, all gate failures, executable sources and completed/interrupted checks. Binaries remain local and are fingerprinted in the raw file. The archived qualification summary and logs distinguish every source and test scope.

Rebuild the saved `current` and `before` C sources with the same interpreter and native settings; use the retained installed artifact for the optional historical control. The saved harness includes its unchanged timing helpers and package support. Run it from a matching checkout or put the extracted `harness` directory first on `PYTHONPATH`:

```sh
python -m bench.profiles.clone_common_cpu \
  --source-dir CURRENT_SNAPSHOT --compare-dir BEFORE_SNAPSHOT \
  --original-dir PUBLISHED_4_0_2_SNAPSHOT \
  --infer-native-bindings --require-native --gc-enabled \
  --consumer-cases --temporal-cases --rounds 7 \
  --max-slowdown 1.10 --target-speedup 100 --output result.json
```

The 100× target is recorded independently from the regression gate. `--enforce-target` additionally fails when any selected case misses100×. This run exits 1 because of the regression failures and retains its complete output. No timing sample or input shape was removed.
