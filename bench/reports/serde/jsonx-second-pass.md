# JSONX second-pass public Python qualification

**The 100× target was not achieved.**

On the existing 500-record profile, default text serialization improves **3.64–4.27×** and compact bytes **4.14–4.50×** versus the original source. The core fixture with varied float scores improves **1.55–1.67×** for text and **1.69–1.95×** for bytes. Validated canonical encoding improves **1.52–1.72×** on the 32-record registry. Many operations remain near parity; all measured losses remain below.

The tables use the unchanged existing public registry, core cases, and Serde profile. Every throughput value measures the public Python call, including dispatch, proofs, formatting/parsing and result allocation. C component probes provide no gain claim here. Python function profiling ran separately after timing. Profiling can change native dispatch observability, so its call counts describe the profiled routes rather than proving a timed native route.

Each cell below is **final/original / final/first-pass**. Ratios below 1 retain measured losses. The estimator is the median across three cyclically rotated source repetitions of the existing minimum calibrated wall-clock batch means; profiler times and CPU diagnostics never replace wall time.

| CPython | Serde providers | Existing profile fixtures |
|---|---|---|
| 3.12.15 | orjson 3.12.0, msgspec 0.22.0, pybase64 1.5.0 | 118 |
| 3.13.16 | orjson 3.12.0, msgspec 0.22.0, pybase64 1.5.0 | 122 |
| 3.14.8 | orjson 3.12.0, msgspec 0.22.0, pybase64 1.5.0 | 118 |

The final Python/C source closure is `914a990687481d7e415d1c2d61f1445591fb2f4db39fad58b7d174ad93471a85`, verified against the [source manifest](jsonx-second-source-manifest.json). A bounded native writer now handles finite floats with CPython-compatible spelling and operation-local caches. `canonical_strict` creates an owned native snapshot, then calls the existing encoder resolved before copying. Legacy canonical encoding reuses its stateless encoder. Encoder hooks, custom behavior, observer-sensitive routes and unsupported inputs retain their existing fallbacks. There is no cross-call result cache.

## Existing public-function registry

| Workload | Python 3.12 | Python 3.13 | Python 3.14 |
|---|---|---|---|
| JSONDecoder | 1.02× / 1.02× | 1.00× / 1.06× | 1.02× / 0.97× |
| JSONEncoder | 1.01× / 0.98× | 0.98× / 1.04× | 1.03× / 1.00× |
| canonical | 1.06× / 1.02× | 1.01× / 0.97× | 0.97× / 0.98× |
| canonical_strict | 1.61× / 1.53× | 1.72× / 1.61× | 1.52× / 1.43× |
| dump | 1.04× / 0.99× | 0.96× / 0.97× | 1.02× / 0.98× |
| dumpb | 1.03× / 1.00× | 0.90× / 0.90× | 1.09× / 1.03× |
| dumps | 2.23× / 0.99× | 2.38× / 0.95× | 2.30× / 1.03× |
| load | 1.05× / 1.01× | 1.00× / 0.97× | 1.03× / 0.96× |
| loads | 1.12× / 0.99× | 1.00× / 0.95× | 0.99× / 0.98× |

## Existing Serde profile workloads

The unchanged full Serde profile runs its own timing pass followed by cProfile. Its original timer retains ns/spread/iterations/rounds summaries. Registry/core evidence additionally retains every raw wall and thread-CPU sample. All existing JSONX profile rows appear below; optional NumPy fixtures run only where NumPy is installed.

| Workload | Python 3.12 | Python 3.13 | Python 3.14 |
|---|---|---|---|
| jsonx.canonical/custom | 0.93× / 0.93× | 1.02× / 1.19× | 1.05× / 1.06× |
| jsonx.canonical/default | 0.99× / 1.00× | 1.05× / 1.06× | 1.09× / 0.99× |
| jsonx.dump/stream | 1.00× / 0.99× | 1.15× / 1.05× | 1.15× / 0.99× |
| jsonx.dumpb/float | 4.25× / 4.13× | 4.53× / 4.54× | 6.29× / 5.99× |
| jsonx.dumpb/int | 1.02× / 1.02× | 0.96× / 0.99× | 0.93× / 0.86× |
| jsonx.dumpb/integers15 | 1.03× / 1.01× | 0.93× / 1.21× | 1.03× / 1.03× |
| jsonx.dumpb/integers8 | 1.00× / 1.00× | 1.02× / 1.18× | 1.02× / 1.02× |
| jsonx.dumpb/null | 1.00× / 1.02× | 1.01× / 0.98× | 1.10× / 0.99× |
| jsonx.dumpb/records | 4.26× / 4.19× | 4.50× / 5.15× | 4.14× / 4.81× |
| jsonx.dumpb/small | 1.06× / 1.02× | 0.99× / 1.03× | 1.00× / 0.96× |
| jsonx.dumpb/string | 0.99× / 1.02× | 0.76× / 0.80× | 0.95× / 0.98× |
| jsonx.dumpb/text | 1.01× / 1.02× | 0.96× / 1.03× | 1.01× / 0.96× |
| jsonx.dumpb/wide | 1.07× / 1.18× | 1.06× / 1.01× | 0.93× / 0.86× |
| jsonx.dumps/class | 1.00× / 1.00× | 1.04× / 1.08× | 1.09× / 1.00× |
| jsonx.dumps/compact | 1.26× / 1.00× | 1.21× / 1.07× | 1.20× / 1.01× |
| jsonx.dumps/custom | 0.98× / 1.01× | 1.27× / 1.04× | 1.25× / 0.97× |
| jsonx.dumps/float | 2.06× / 2.07× | 1.79× / 1.82× | 2.00× / 2.07× |
| jsonx.dumps/indent | 2.97× / 0.98× | 2.93× / 1.00× | 3.21× / 1.05× |
| jsonx.dumps/indent4 | 1.00× / 1.00× | 1.05× / 0.96× | 1.08× / 0.97× |
| jsonx.dumps/int | 1.55× / 1.08× | 1.16× / 0.91× | 1.33× / 0.98× |
| jsonx.dumps/integers15 | 1.32× / 1.01× | 1.26× / 1.19× | 1.23× / 1.02× |
| jsonx.dumps/integers8 | 1.44× / 1.07× | 1.32× / 1.09× | 1.29× / 1.05× |
| jsonx.dumps/null | 1.55× / 1.04× | 1.31× / 1.02× | 1.35× / 1.04× |
| jsonx.dumps/numpy | — (optional fixture absent) | 0.97× / 1.02× | — (optional fixture absent) |
| jsonx.dumps/numpy-default | — (optional fixture absent) | 1.02× / 1.04× | — (optional fixture absent) |
| jsonx.dumps/numpy-error | — (optional fixture absent) | 1.06× / 0.99× | — (optional fixture absent) |
| jsonx.dumps/numpy-scalar | — (optional fixture absent) | 1.03× / 1.04× | — (optional fixture absent) |
| jsonx.dumps/records | 3.64× / 3.64× | 3.71× / 4.71× | 4.27× / 3.54× |
| jsonx.dumps/small | 1.40× / 1.05× | 1.38× / 1.10× | 1.37× / 1.02× |
| jsonx.dumps/sorted | 1.15× / 0.93× | 1.17× / 0.98× | 1.48× / 0.94× |
| jsonx.dumps/string | 1.00× / 1.01× | 0.91× / 1.00× | 0.90× / 1.24× |
| jsonx.dumps/text | 1.33× / 1.00× | 1.28× / 1.01× | 1.24× / 0.99× |
| jsonx.dumps/wide | 1.24× / 1.10× | 1.16× / 1.23× | 1.07× / 0.97× |
| jsonx.load/stream | 1.29× / 1.00× | 1.03× / 1.08× | 1.02× / 1.04× |
| jsonx.loads/error | 1.08× / 0.94× | 0.98× / 1.00× | 1.02× / 1.03× |
| jsonx.loads/float | 1.01× / 1.00× | 1.02× / 1.02× | 0.90× / 0.85× |
| jsonx.loads/float/bytes | 1.02× / 0.97× | 1.06× / 1.07× | 0.83× / 0.98× |
| jsonx.loads/hook | 1.06× / 0.96× | 1.06× / 1.05× | 1.03× / 1.33× |
| jsonx.loads/integers | 0.99× / 1.02× | 1.00× / 1.01× | 1.07× / 1.13× |
| jsonx.loads/integers/bytes | 0.99× / 0.99× | 1.00× / 1.03× | 1.10× / 1.07× |
| jsonx.loads/records | 1.02× / 0.99× | 1.00× / 1.12× | 1.09× / 1.23× |
| jsonx.loads/records/bytes | 1.00× / 0.98× | 0.98× / 1.10× | 1.11× / 1.01× |
| jsonx.loads/small | 1.03× / 1.01× | 1.04× / 1.08× | 1.23× / 1.13× |
| jsonx.loads/small/bytes | 1.01× / 0.97× | 1.07× / 1.27× | 1.12× / 1.09× |
| jsonx.loads/string | 1.06× / 0.97× | 0.95× / 1.05× | 1.11× / 1.07× |
| jsonx.loads/string/bytes | 1.02× / 0.96× | 0.99× / 1.03× | 1.02× / 1.10× |

## Existing core cases and stdlib controls

Each cell gives final/original / final/first-pass, followed by the same ratios for the unchanged stdlib control. Control movement records variation in the run context; it does not erase a public-call regression.

| Workload | Python 3.12 | Python 3.13 | Python 3.14 |
|---|---|---|---|
| jsonx.dumpb · 500 records | 1.69× / 1.75× (stdlib 0.99× / 1.03×) | 1.94× / 1.87× (stdlib 1.01× / 1.18×) | 1.95× / 1.95× (stdlib 1.02× / 0.98×) |
| jsonx.dumps · 32-int record | 1.21× / 1.03× (stdlib 1.01× / 0.92×) | 1.18× / 1.06× (stdlib 1.06× / 1.10×) | 1.34× / 1.07× (stdlib 1.17× / 1.00×) |
| jsonx.dumps · 500 records | 1.55× / 1.53× (stdlib 1.03× / 1.01×) | 1.67× / 1.80× (stdlib 0.94× / 0.95×) | 1.65× / 1.57× (stdlib 0.99× / 0.95×) |
| jsonx.dumps · text payload | 1.31× / 1.00× (stdlib 1.04× / 1.01×) | 1.33× / 1.00× (stdlib 0.97× / 1.02×) | 1.27× / 0.97× (stdlib 1.06× / 0.95×) |
| jsonx.loads · 1000 integers | 0.98× / 0.98× (stdlib 1.04× / 1.01×) | 0.99× / 1.02× (stdlib 1.02× / 1.03×) | 1.06× / 1.05× (stdlib 0.93× / 0.93×) |
| jsonx.loads · 20 nested records | 1.01× / 1.02× (stdlib 1.01× / 1.00×) | 1.02× / 1.03× (stdlib 1.02× / 1.07×) | 0.96× / 0.95× (stdlib 0.95× / 0.91×) |
| jsonx.loads · 500 records | 1.06× / 1.01× (stdlib 1.05× / 1.01×) | 0.92× / 0.94× (stdlib 1.01× / 1.00×) | 1.08× / 1.00× (stdlib 1.00× / 0.99×) |

Every candidate core measurement met its unchanged floor. One original-source measurement on Python 3.13, repetition 3, missed the existing 4.19× stdlib-relative floor for `jsonx.dumps · text payload`; that baseline exception remains in the raw report. No floor was edited.

## Losses, spread and profile evidence

| CPython | Registry/core >25% spread | Maximum | JSONX profile >25% spread | Maximum |
|---|---|---|---|---|
| 312 | 32/207 | 277% | 23/378 | 139% |
| 313 | 33/207 | 316% | 35/414 | 356% |
| 314 | 40/207 | 664% | 60/378 | 545% |

| CPython | Group | Compared with | Slowest ratio workload | Ratio |
|---|---|---|---|---|
| 312 | public_registry | original | unstd.serde.jsonx.JSONEncoder | 1.01× |
| 312 | public_registry | first pass | unstd.serde.jsonx.JSONEncoder | 0.98× |
| 312 | core_registry | original | jsonx.loads · 1000 integers | 0.98× |
| 312 | core_registry | first pass | jsonx.loads · 1000 integers | 0.98× |
| 312 | existing_serde_profile | original | jsonx.canonical/custom | 0.93× |
| 312 | existing_serde_profile | first pass | jsonx.canonical/custom | 0.93× |
| 313 | public_registry | original | unstd.serde.jsonx.dumpb | 0.90× |
| 313 | public_registry | first pass | unstd.serde.jsonx.dumpb | 0.90× |
| 313 | core_registry | original | jsonx.loads · 500 records | 0.92× |
| 313 | core_registry | first pass | jsonx.loads · 500 records | 0.94× |
| 313 | existing_serde_profile | original | jsonx.dumpb/string | 0.76× |
| 313 | existing_serde_profile | first pass | jsonx.dumpb/string | 0.80× |
| 314 | public_registry | original | unstd.serde.jsonx.canonical | 0.97× |
| 314 | public_registry | first pass | unstd.serde.jsonx.load | 0.96× |
| 314 | core_registry | original | jsonx.loads · 20 nested records | 0.96× |
| 314 | core_registry | first pass | jsonx.loads · 20 nested records | 0.95× |
| 314 | existing_serde_profile | original | jsonx.loads/float/bytes | 0.83× |
| 314 | existing_serde_profile | first pass | jsonx.loads/float | 0.85× |

All losses, profile call counts/self/cumulative times, native source/binary hashes, interpreter and installed backend code fingerprints remain in the raw evidence. Spread above 25% marks noisy observations; it is not a confidence interval. Small ratios around parity and any matching control movement need cautious interpretation.

## Separate regression follow-up

The primary Python 3.13 `jsonx.dumpb/string` loss remains **0.762× / 0.798×** versus original/first pass. Its candidate repetitions were 87.58, 63.19 and 117.93 ns with 73%, 6% and 155% spread; the original repetitions were 66.47–67.21 ns. The existing full profile retains summary statistics rather than CPU samples, so it cannot establish whether those slow repetitions were descheduled.

A separate experiment reuses the same existing `Workload.call`, registry `Case.run`, and core partials. Six cyclic source repetitions balance each source position twice; each measurement retains seven wall-clock samples and the available thread-CPU samples. Case order rotates identically for all sources within a repetition. The primary matrix above is unchanged.

Cells below give **primary wall ratio → follow-up wall ratio**, each as final/original / final/first-pass. CPU ratios are separate diagnostics and never substitute for wall time.

| CPython | Existing call | Primary wall | Follow-up wall | Follow-up thread CPU diagnostic |
|---|---|---|---|---|
| 313 | core:jsonx.loads · 500 records | 0.918× / 0.940× | 0.996× / 0.995× | 0.993× / 0.992× |
| 313 | profile:jsonx.dumpb/int | 0.964× / 0.985× | 1.019× / 1.053× | 1.018× / 1.058× |
| 313 | profile:jsonx.dumpb/small | 0.994× / 1.032× | 1.022× / 1.039× | 1.031× / 1.040× |
| 313 | profile:jsonx.dumpb/string | 0.762× / 0.798× | 1.022× / 1.031× | 1.020× / 1.041× |
| 313 | profile:jsonx.dumps/string | 0.908× / 1.004× | 1.007× / 0.993× | 1.008× / 0.999× |
| 313 | profile:jsonx.loads/records | 0.998× / 1.118× | 0.989× / 1.008× | 0.988× / 1.007× |
| 313 | profile:jsonx.loads/small | 1.045× / 1.083× | 1.011× / 0.979× | 1.025× / 0.979× |
| 313 | registry:unstd.serde.jsonx.dumpb | 0.900× / 0.903× | 0.981× / 0.991× | 0.960× / 0.970× |
| 313 | registry:unstd.serde.jsonx.dumps | 2.384× / 0.946× | 2.217× / 0.998× | 2.247× / 1.004× |
| 313 | registry:unstd.serde.jsonx.loads | 0.996× / 0.948× | 1.003× / 1.028× | 1.005× / 1.027× |
| 313 | stdlib:jsonx.loads · 500 records | 1.008× / 1.000× | 1.000× / 1.001× | 1.000× / 1.003× |
| 314 | core:jsonx.loads · 20 nested records | 0.963× / 0.950× | 0.997× / 1.010× | 0.998× / 1.017× |
| 314 | profile:jsonx.dumpb/int | 0.931× / 0.860× | 0.997× / 1.010× | 0.998× / 0.999× |
| 314 | profile:jsonx.dumpb/string | 0.949× / 0.980× | 1.008× / 0.985× | 1.013× / 1.002× |
| 314 | profile:jsonx.dumpb/wide | 0.928× / 0.863× | 0.988× / 1.021× | 0.956× / 1.054× |
| 314 | profile:jsonx.loads/float | 0.899× / 0.852× | 1.046× / 1.024× | 1.047× / 1.025× |
| 314 | profile:jsonx.loads/float/bytes | 0.833× / 0.976× | 1.017× / 1.039× | 1.014× / 1.036× |
| 314 | profile:jsonx.loads/string | 1.107× / 1.066× | 1.060× / 1.037× | 1.045× / 1.040× |
| 314 | profile:jsonx.loads/string/bytes | 1.016× / 1.097× | 1.053× / 1.065× | 1.052× / 1.067× |
| 314 | registry:unstd.serde.jsonx.dumpb | 1.088× / 1.028× | 0.994× / 1.007× | 0.989× / 1.002× |
| 314 | registry:unstd.serde.jsonx.loads | 0.992× / 0.975× | 0.999× / 1.017× | 0.999× / 1.017× |
| 314 | stdlib:jsonx.loads · 20 nested records | 0.954× / 0.914× | 0.996× / 1.002× | 0.994× / 1.003× |

The Python 3.13 string fixture measures 65.17 / 65.75 / 63.77 ns for original / first pass / final in the follow-up, with matching CPU diagnostics. Its primary material slowdown did not reproduce. The 3.13 core 500-record decode measures 116.10 / 115.93 / 116.54 µs; its stdlib control is 201.33 / 201.46 / 201.30 µs. Python 3.14 float decode also returns near parity in the follow-up.

No sustained material slowdown reproduced in these selected calls. This narrower experiment changes order and context; it does not establish the cause of the primary losses or replace those results. Follow-up batches also contain large outliers, retained in the raw evidence. Unrepeated losses elsewhere remain unresolved, including the 3.12 custom canonical row.

The [Python 3.13 follow-up](jsonx-second-followup-313.json) and [Python 3.14 follow-up](jsonx-second-followup-314.json) include the complete focused driver source, exact commands, unchanged harness/source identities and every wall/CPU sample.

## Reproduce and correctness gates

The original source was saved before edits at `94589856d2e604310b55020e40c51010f2af4755`. The first-pass source and final source are separate immutable snapshots. Freeze the same existing harness and build each source’s native extension for the selected interpreter before running the retained driver.

The [first-pass text source archive](jsonx-first-pass-source.tar.gz) and [byte-identical frozen harness](jsonx-existing-harness.tar.gz) preserve the inputs after temporary directories are removed. The [reproduction manifest](jsonx-second-reproduction.json) records every archived file hash and overlay instructions. The original source comes from the commit above; the final source is the reviewed tree identified by its source manifest. Archives contain no compiled extensions.

The retained [comparison driver](../../profiles/jsonx_existing.py) selects product and harness paths before worker imports. Keep one interpreter and dependency installation fixed across all three sources. The report records actual executable/libpython hashes and installed provider code/binary fingerprints; workers reject changes within or between repetitions. Source-only archives must be rebuilt for the selected interpreter.

```console
python bench/profiles/jsonx_existing.py --original /saved/original/src --first-pass /saved/first-pass/src --candidate /saved/final/src --harness /saved/harness --rounds 5 --profile-rounds 3 --repetitions 3 --output /results/jsonx-existing.json
python bench/profiles/jsonx_existing.py --original /saved/original/src --first-pass /saved/first-pass/src --candidate /saved/final/src --harness /saved/harness --smoke --rounds 1 --profile-rounds 1 --repetitions 1 --output /results/jsonx-smoke.json
PYTHONPATH=src:. python -m pytest tests/suites/assurance/gates/bench/test_jsonx_existing.py -q
ruff check bench/profiles/jsonx_existing.py tests/suites/assurance/gates/bench/test_jsonx_existing.py
ruff format --check bench/profiles/jsonx_existing.py tests/suites/assurance/gates/bench/test_jsonx_existing.py
MYPYPATH=src mypy --no-incremental --cache-dir /tmp/jsonx-driver-mypy-cache --follow-imports=silent bench/profiles/jsonx_existing.py
```

The driver runs the unchanged `bench.speeds.timings`, `bench.core.report.run`, and full `bench.profiles.serde_profile._worker` with independent audits. Its separate ten-pass registry Python profile also covers `canonical_strict`. The correctness gates reject corrupted outputs, mismatched imports/native closures, fixture/runtime drift and CPU-based concealment of wall-time losses; they run no performance threshold.

The [14 driver correctness gates](../../../tests/suites/assurance/gates/bench/test_jsonx_existing.py) passed on the three stable runtimes. Ruff and formatting passed. The focused mypy check uses only the driver as a checked root, with imported Python modules followed silently; this does not claim the complete product/harness type gate passes in the minimal environment. Exact validation commands and scope remain in the [evidence manifest](jsonx-second-pass-evidence.json).

No existing JSONX fixture, floor or latency ceiling was changed.

## Raw evidence

- Python 312: [jsonx-second-pass-312.json](jsonx-second-pass-312.json)
- Python 313: [jsonx-second-pass-313.json](jsonx-second-pass-313.json)
- Python 314: [jsonx-second-pass-314.json](jsonx-second-pass-314.json)

The [evidence manifest](jsonx-second-pass-evidence.json) indexes source identities, interpreter/backend fingerprints, validation scope, losses, floor exceptions and follow-up reports.

Safety qualification: [second-pass JSONX safety](jsonx-second-safety.md), [native safety results](jsonx-second-native-safety.json).
