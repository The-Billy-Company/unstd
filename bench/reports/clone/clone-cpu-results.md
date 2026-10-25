# Clone CPU optimization measurements

CPU and wall-clock evidence measured on 2026-10-25, using CPython 3.14.8 and 3.13.16 on an arm64 Mac running macOS 26.6. These are paired measurements against the existing native implementation at commit `94589856d2e604310b55020e40c51010f2af4755`, not against an artificially slowed Python baseline.

The self-cycle case improves **239–311× in process CPU with GC disabled**, and **225–308× with GC enabled**. Flat tuples improve about **3.9–4.2×**, the 100,000-item scalar list improves **3.7–3.8×**, nested records improve **1.4–1.5×**, and dataclass data payloads improve **1.6–1.8×**. These results support a 100× improvement for the formerly pathological cyclic fallback only. They do not support a universal 100× claim.

The optimized entry points are `clone.deep` and `clone.asdict`. `clone.deepcopy` remains the direct `copy.deepcopy` alias. Structural cloning duplicates repeated aliases, as before; the stdlib control preserves them. Cycles still use whole-graph stdlib deepcopy, reached without first exhausting Python recursion when the native proof detects a builtin cycle.

## Method

- All 19 inputs are constructed before timing. Exact types, nested values, dictionary key types/order, independence, tuple reuse and alias behavior are checked outside timing against an independently constructed stdlib result.
- Current native, saved original native, current Python-only and stdlib operations run in the same process. The order reverses on alternate rounds. Each callable receives calibrated batches of at least 5 ms and nine measured rounds; the tables use median batch means.
- Each batch records `time.process_time_ns()` and `time.perf_counter_ns()`. Fresh outputs are destroyed in the measured loop; cyclic outputs are reclaimed by collection when enabled or after the workload. This measures CPU work as well as elapsed latency, using one process and no GPU or worker threads.
- GC-disabled runs match the main benchmark convention. Separate GC-enabled runs retain the real collector thresholds, `(2000, 10, 10)`. Profiles run separately from timing, three operations per profile. Every JSON retains all samples, calibration counts, source/extension hashes, Python-only controls and top Python/C profile entries.
- The root native probe reports root eligibility only. Dataclass and `asdict` payloads can use a native call below an unsupported root; their separate profiles record those calls.

## GC disabled: CPython 3.14.8

All absolute times below are microseconds per operation. A gain below 1× is a slowdown. “vs stdlib” compares `asdict` to `dataclasses.asdict` and every other row to `copy.deepcopy`.

| Workload | Before CPU | After CPU | CPU gain | CPU vs stdlib | After wall | Wall gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.072 | 0.074 | 0.97× | 6.19× | 0.076 | 0.96× |
| flat-list-1000 | 2.396 | 2.312 | 1.04× | 12.08× | 2.367 | 1.05× |
| flat-dict-1000 | 5.490 | 5.138 | 1.07× | 11.63× | 5.170 | 1.07× |
| flat-tuple-1000 | 9.521 | 2.315 | 4.11× | 16.39× | 2.320 | 4.16× |
| nested-200 | 35.238 | 24.898 | 1.42× | 5.11× | 25.097 | 1.41× |
| records-500 | 29.812 | 26.184 | 1.14× | 7.30× | 26.198 | 1.14× |
| tuple-records-500 | 55.586 | 27.875 | 1.99× | 11.75× | 27.871 | 2.00× |
| wide-lists-1000 | 32.359 | 30.668 | 1.06× | 7.66× | 30.688 | 1.06× |
| deep-80 | 3.946 | 4.106 | 0.96× | 4.27× | 4.109 | 0.96× |
| large-list-100000 | 1137.750 | 297.950 | 3.82× | 8.42× | 297.950 | 3.89× |
| large-records-20000 | 1070.125 | 993.750 | 1.08× | 7.11× | 1001.583 | 1.07× |
| repeated-aliases-500 | 19.305 | 17.953 | 1.08× | 1.63× | 17.983 | 1.07× |
| dataclasses-200 | 80.603 | 83.904 | 0.96× | 3.44× | 83.937 | 0.96× |
| dataclass-records-500 | 102.426 | 61.641 | 1.66× | 5.04× | 61.640 | 1.66× |
| mixed-keys-1000 | 103.793 | 102.562 | 1.01× | 3.00× | 102.803 | 1.02× |
| foreign-last | 15.356 | 15.200 | 1.01× | 1.82× | 15.206 | 1.01× |
| asdict-list-100000 | 1144.750 | 314.321 | 3.64× | 9.78× | 315.280 | 3.65× |
| asdict-records-500 | 120.500 | 68.367 | 1.76× | 2.71× | 69.384 | 1.74× |
| cycle | 150.838 | 0.485 | 311.13× | 0.73× | 0.493 | 309.81× |

## GC disabled: CPython 3.13.16

| Workload | Before CPU | After CPU | CPU gain | CPU vs stdlib | After wall | Wall gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.088 | 0.080 | 1.11× | 10.99× | 0.080 | 1.10× |
| flat-list-1000 | 2.381 | 2.284 | 1.04× | 36.95× | 2.299 | 1.04× |
| flat-dict-1000 | 5.254 | 5.081 | 1.03× | 35.14× | 5.159 | 1.03× |
| flat-tuple-1000 | 9.172 | 2.351 | 3.90× | 39.10× | 2.388 | 3.95× |
| nested-200 | 34.953 | 23.969 | 1.46× | 8.48× | 24.368 | 1.46× |
| records-500 | 31.645 | 27.066 | 1.17× | 14.83× | 27.515 | 1.17× |
| tuple-records-500 | 61.446 | 32.016 | 1.92× | 17.28× | 33.637 | 1.91× |
| wide-lists-1000 | 35.766 | 31.770 | 1.13× | 11.54× | 35.397 | 1.09× |
| deep-80 | 4.133 | 4.343 | 0.95× | 4.51× | 4.627 | 0.93× |
| large-list-100000 | 1224.667 | 333.167 | 3.68× | 26.18× | 374.319 | 3.60× |
| large-records-20000 | 1217.500 | 1114.667 | 1.09× | 12.40× | 1235.528 | 1.09× |
| repeated-aliases-500 | 21.461 | 19.104 | 1.12× | 1.68× | 20.856 | 1.10× |
| dataclasses-200 | 112.020 | 107.673 | 1.04× | 4.28× | 123.396 | 1.11× |
| dataclass-records-500 | 142.559 | 78.594 | 1.81× | 7.73× | 96.036 | 1.68× |
| mixed-keys-1000 | 143.188 | 142.387 | 1.01× | 4.50× | 148.679 | 0.99× |
| foreign-last | 18.463 | 17.281 | 1.07× | 5.81× | 18.713 | 1.05× |
| asdict-list-100000 | 1166.500 | 317.769 | 3.67× | 9.69× | 329.772 | 3.57× |
| asdict-records-500 | 123.963 | 64.547 | 1.92× | 3.02× | 65.676 | 1.92× |
| cycle | 157.552 | 0.660 | 238.61× | 0.65× | 0.754 | 219.75× |

## GC enabled controls

Both versions retain the large tuple, scalar-list, nested-record and cyclic gains when collection is enabled. The table includes all fallback controls as well as successful fast paths.

| Workload | 3.14 CPU after, µs | 3.14 CPU gain | 3.13 CPU after, µs | 3.13 CPU gain | 3.14 wall gain | 3.13 wall gain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small-dict | 0.091 | 0.85× | 0.084 | 1.05× | 0.84× | 1.05× |
| flat-list-1000 | 2.248 | 1.05× | 2.576 | 1.00× | 1.05× | 0.98× |
| flat-dict-1000 | 5.298 | 1.01× | 5.201 | 1.02× | 1.02× | 1.02× |
| flat-tuple-1000 | 2.531 | 4.20× | 2.390 | 3.89× | 4.23× | 3.86× |
| nested-200 | 24.715 | 1.40× | 24.586 | 1.50× | 1.40× | 1.50× |
| records-500 | 26.352 | 1.12× | 29.742 | 1.08× | 1.12× | 1.09× |
| tuple-records-500 | 29.332 | 1.95× | 30.016 | 1.97× | 1.98× | 1.98× |
| wide-lists-1000 | 32.508 | 1.05× | 31.957 | 1.11× | 1.04× | 1.11× |
| deep-80 | 4.343 | 0.98× | 4.144 | 0.99× | 0.97× | 0.99× |
| large-list-100000 | 305.321 | 3.69× | 295.316 | 3.84× | 3.68× | 3.82× |
| large-records-20000 | 1479.500 | 1.00× | 1085.875 | 1.09× | 0.99× | 1.09× |
| repeated-aliases-500 | 19.260 | 1.05× | 17.918 | 1.12× | 1.04× | 1.13× |
| dataclasses-200 | 88.032 | 0.98× | 94.294 | 1.03× | 0.98× | 1.04× |
| dataclass-records-500 | 67.227 | 1.64× | 67.375 | 1.76× | 1.63× | 1.77× |
| mixed-keys-1000 | 108.159 | 1.01× | 123.795 | 0.99× | 1.01× | 0.99× |
| foreign-last | 14.772 | 1.03× | 19.541 | 0.95× | 1.03× | 0.91× |
| asdict-list-100000 | 305.579 | 3.68× | 360.417 | 3.45× | 3.68× | 3.57× |
| asdict-records-500 | 66.109 | 1.71× | 71.103 | 1.84× | 1.71× | 1.79× |
| cycle | 0.505 | 308.25× | 0.641 | 224.59× | 310.02× | 219.86× |

## Limits and regressions

The 80-level tree takes about 1–5% more CPU across these runs. Many tiny dataclasses are 2–4% slower on 3.14 and 3–4% faster on 3.13. Mixed-key and foreign-node controls remain close to the previous implementation; the 3.13 GC-enabled foreign-last sample is about 6% slower in CPU and 10% slower in wall time. The tiny dictionary is 15% slower in the 3.14 GC-enabled run, an absolute difference of about 14 ns. These measurements are retained rather than dropped from the report.

The 20,000-record case gains about 8–9% with GC disabled, but is effectively unchanged in CPU on 3.14 with GC enabled. CPU and wall ratios can differ because of descheduling, collection and allocator effects; individual samples remain available for inspection. The cycle path is still slower than calling stdlib deepcopy directly, because `deep` must detect and route the cycle.

The Python-only controls do not receive these native gains. Builtin depth and container budgets, foreign types, custom copy hooks, explicit fallback rules and optional-extension availability still bound where acceleration applies.

## Profile evidence and implementation changes

For three copies of the self-cycle, the original implementation enters `_walk` **2,982 times** before whole-graph deepcopy; the optimized implementation enters it **zero times**. For the dataclass containing 500 nested data records, `_walk` calls drop from **3,009 to 3**, with three native calls. For the equivalent `asdict` payload, `_plain` calls drop from **3,006 to 3**, also with three native calls. These are call counts from the separate 3.14 profiles, not timing claims derived from profiler overhead.

The C walker now allocates sequence outputs at their final size, builds tuples directly, admits large atomic payloads using a budget on containers, and proves builtin cycles before copying. Exact `dict.items` descriptor resolution is reused within a tree while owned item tuples preserve reference safety across GC callbacks. Python dataclass/model state and `asdict` field boundaries can send eligible data subtrees through the same kernel.

## Validation

The clone suite passed on CPython 3.12 (180 passed, 10 optional skips), 3.13 (190 passed) and 3.14. The combined 3.14 clone and benchmark gate run passed 365 tests with 46 skips and two deselections. The 190 clone tests also passed under AddressSanitizer and UndefinedBehaviorSanitizer with `PYTHONMALLOC=malloc`. Ruff, formatting and focused mypy checks passed. These checks complement the untimed correctness checks in every measurement; they do not turn the performance measurements into guarantees for unmeasured shapes.

## Reproduction and provenance

Raw evidence: [3.14 GC disabled](clone-cpu-py314.json), [3.14 GC enabled](clone-cpu-py314-gc.json), [3.13 GC disabled](clone-cpu-py313.json), [3.13 GC enabled](clone-cpu-py313-gc.json). The [driver](../../profiles/clone_cpu.py) requires no optional model dependency.

Build the current editable package for the selected CPython interpreter using the project build hook, and confirm `unstd.clone._clone_native` imports. For example, `uv pip install --python /path/to/python --no-deps -e .` builds the optional kernels. To recreate the original clone source and matching native extension without keeping the temporary snapshot used here:

```sh
export CLONE_BEFORE_DIR="$(mktemp -d)"
git show 94589856d2e604310b55020e40c51010f2af4755:src/unstd/clone/structural.py > "$CLONE_BEFORE_DIR/structural.py"
git show 94589856d2e604310b55020e40c51010f2af4755:native/clone/src/_clone_native.c > "$CLONE_BEFORE_DIR/_clone_native.c"
python - <<'PY'
import os
from pathlib import Path
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext
directory = Path(os.environ["CLONE_BEFORE_DIR"])
extension = Extension("_clone_native", [str(directory / "_clone_native.c")])
command = build_ext(Distribution({"ext_modules": [extension]}))
command.build_lib = str(directory)
command.build_temp = str(directory / "build")
command.ensure_finalized()
command.run()
PY
PYTHONPATH=src python -m bench.profiles.clone_cpu --compare-dir "$CLONE_BEFORE_DIR" --rounds 9 --output /tmp/clone-cpu.json
PYTHONPATH=src python -m bench.profiles.clone_cpu --compare-dir "$CLONE_BEFORE_DIR" --rounds 9 --gc-enabled --output /tmp/clone-cpu-gc.json
```

Repeat with each Python version; the snapshot loader selects that interpreter’s extension ABI. The saved original C and Python sources were verified byte-for-byte against the baseline commit. Each report records matching source, compiled-extension and harness SHA-256 fingerprints. The source fingerprints for this run are:

| File | SHA-256 |
| --- | --- |
| Original structural.py | `36c324266e9b13f4dabe5f8ea28092e9d5e771fa25085458709fe41c7899e3c0` |
| Original _clone_native.c | `4d48de30f226bd4f4c5f95b473a7572bf74c7a3b1308e9aacbd68b029009e443` |
| Current structural.py | `df77a9d7236696f64fbe71266c3403459ce9099357cd03d48f1e6c59301489b4` |
| Current _clone_native.c | `c47c0572b72883e4f730ad506a56e937ef11f9dd852f99fd5094aed59a39fc18` |
| Measured clone_cpu.py | `242b5675f51125f721782d1781c169cb858addab19c5f589d1ee9b6d77db9638` |

The benchmark checker and operation selection have 11 focused gate tests covering exact shapes, key order/types, nested isolation, tuple/alias behavior, `deep` versus `asdict` dispatch, and baseline ABI/hash selection. All 19 workloads passed the strict checks in all four measured runs.
