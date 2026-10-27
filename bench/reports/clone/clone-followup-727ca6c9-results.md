# Clone follow-up: reduced CPU overhead and temporal targets

The final focused C 727 run brings all three original CPU regressions inside the 1.10 allowance. CPU time remains 4.6–5.8% above before: 1.05763× for the small dataclass, 1.04632× for datetime rows and 1.05456× for the copy hook. The datetime-row case is 1.28× faster than the first frozen candidate on the matched CPU comparison.

The formal combined gate still fails: small-dataclass wall slowdown is 1.29954× and copy-hook wall slowdown is 1.22062×. We retain both failures. The final UTC datetime1k focus exceeds 100× on CPU and wall, but that separate rerun does not replace its earlier 60.705× wall miss.

## Final focused results

| Final C 727 case | Current CPU µs | CPU slowdown vs before | Wall slowdown vs before | CPU speedup vs warm efb | vs stdlib CPU | vs stdlib wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `dataclass-small` | 0.759 | 1.05763× | 1.29954× | 1.023× | 3.62× | 3.50× |
| `dataclasses-datetime-200` | 186.911 | 1.04632× | 0.95312× | 1.280× | 4.85× | 5.76× |
| `consumer-copy-hook-alias` | 1.339 | 1.05456× | 1.22062× | 1.023× | 1.23× | 1.08× |
| `temporal-datetime-utc-1000` | 7.507 | 0.03464× | 0.03922× | 1.220× | 159.57× | 228.21× |

These four cases use final C `727ca6c9…` and Python `32a555b2…`. All strict output checks and the actual native model/closed-temporal route proofs pass. A fresh full 57 comparison was not run on this source; the first candidate's fourteen wall failures remain in the [first frozen report](clone-consumer-temporal-efb2ae10-results.md).

## Temporal14 results and scope

The preceding fourteen-case run on C `0bd26032…` passes the 1.10 regression guard on both clocks for every case. All ten homogeneous lists exceed 100× CPU, from 123.1× to 189.7×. Nine homogeneous lists plus the tuple-date control meet 100× on both clocks in that run.

| C 0bd temporal case | vs stdlib CPU | vs stdlib wall | 100× both clocks |
| --- | ---: | ---: | --- |
| `date-1000` | 155.80× | 271.65× | met |
| `date-10000` | 189.74× | 229.64× | met |
| `datetime-naive-1000` | 153.81× | 210.71× | met |
| `datetime-naive-10000` | 157.19× | 159.37× | met |
| `datetime-utc-1000` | 157.91× | 60.71× | miss |
| `datetime-utc-10000` | 165.56× | 164.12× | met |
| `time-naive-1000` | 123.08× | 145.95× | met |
| `time-naive-10000` | 123.88× | 100.29× | met |
| `time-utc-1000` | 125.45× | 148.11× | met |
| `time-utc-10000` | 130.40× | 145.96× | met |
| `mixed-scalars-1000` | 41.53× | 40.64× | miss |
| `tuple-date-1000` | 196.93× | 193.57× | met |
| `nested-date-1000` | 4.05× | 6.32× | miss |
| `scalar-first-1000` | 4.68× | 4.91× | miss |

The UTC datetime1k case misses wall at 60.705× in this complete run, despite 157.907× CPU. The subsequent final-source focus reports 159.573× CPU and 228.209× wall; both observations remain visible. We do not combine selected winners from different runs into a claim that one full run met every temporal target.

The exact `clone_temporal_sequence` definition is byte-identical between C 0bd and C 727. The later changes pin the entry walker's code and preserve two rare callback/exception behaviors. The saved continuity hash supports that narrow source statement; it does not assert whole-module binary or all-entrypoint performance equivalence. The scalar-first control is close to the guard at 1.09988× CPU slowdown.

## Preparation and controls

Every follow-up uses the same CPython 3.14.9/Pydantic 2.13.5 environment, public entrypoints, normal GC, nine alternating-order rounds and 200ms calibrated batches. Input construction and correctness checks remain outside timing; output destruction stays included. The normal timing algorithm is unchanged; the archived driver changes only its batch target from 50ms to 200ms.

The performance source/control extensions use matched manual `-O3` builds with assertions enabled. Symbol inspection confirms an imported `assert_rtn` in those binaries; the installed published 4.0.2 artifact lacks it. The before/current source comparisons retain matched compiler settings, while comparisons with that published artifact include a compiler-setting confound.

`before` is the pre-task source. `original` means the first frozen efb candidate in these follow-ups; published 4.0.2 remains preserved in the earlier full 57 evidence. The first three-case follow-up left that original candidate's model marker cold. Its before comparisons are valid, while its original small-dataclass/copy-hook contrasts understate the warm candidate's overhead.

The corrected harness warms current, before, original and stdlib with the same checked private/public-alias model before any timed case. It records each capability and marker state. Shallow copies fail this preparation check. The following three-case, fourteen-case and final four-case reports all retain those warmup records.

## Qualification and limits

Final C 727 passes normal clone tests on Python 3.14 (644 passed, 43 skipped), Python 3.12 (646 passed, 41 skipped) and Python 3.13 (646 passed, 41 skipped). A focused ASan+UBSan run with forced GC on that working C source passes 40 tests with no skips in 55.94 seconds. The archive retains its compiler manifest, instrumented source, collection injector and pytest log.

A clone-only build through the normal `hatch_build.CustomBuildHook._build_module` owner, with real WheelBuilder configuration, metadata and its normal compiler invocation, also passes the full suite: 644 tests passed, 43 skipped in 36.56 seconds. It uses normal interpreter compiler flags including `-DNDEBUG` and installs artifact `e22bba71…`; the timed manual-build artifact `ef0fa7aa…` remains retained. This is build qualification only. The reported timings apply to the retained manual-build binaries; release-artifact performance was not measured.

Commit `d4fc30e` contains owned-only C `9c4dbd6a…`, which excludes the pre-existing unrelated C edits retained in both timed source arms. That source passes 210 ordinary tests with 33 skips and 82 forced-GC tests with no skips. These separate correctness builds use the same Python source and leave the actual timed C 727 binary unchanged; the owned-only build was not separately benchmarked.

The archive keeps exact old and new test bytes and hashes. It retains the 500 generated exact-set diagnostic cases, public policy-trace proof and the initial policy-only type repair, which passes 23 focused tests. The later four-file test-only repair passes strict Mypy across those tests and the runtime source, Ruff, and 85 focused tests with six forced-GC skips. Existing callback, exception, type and alias oracles remain intact. These narrow type-repair receipts and the separate 110-passed/6-skipped focused result are explicitly recorded-run evidence; their raw pytest logs were not captured.

The final benchmark startup overlapped normal-unit stdout activity from 06:03:41.227 to 06:04:09.337 UTC. That observed file interval is not an exact CPU-duration measurement. The saved load receipts show a busy shared laptop; every calibration probe and measured round remains unchanged. The completed focused sanitizer and forced-GC checks do not complete the earlier broad forced-GC stress run, whose interruption remains documented in the first report.

All thirteen consumers missed 100× in the first full 57 run. This follow-up retests the copy-hook consumer and targeted safety/performance cases; it does not claim fresh all-consumer gains. Mixed-scalar, nested and scalar-first temporal controls remain below 100×. These are local measurements with explicit source scopes, not a universal speed guarantee.

[Temporal14 raw rounds](clone-followup-727ca6c9-temporal14-long.json), [final four-case raw rounds](clone-followup-727ca6c9-final-four-long.json), [receipt](clone-followup-727ca6c9-receipt.json) and [complete profile evidence](clone-followup-727ca6c9-evidence.tar.gz) preserve all intermediate/final runs, both harness versions, exact sources, controls, load/overlap records and qualifications. Binaries are fingerprinted and retained locally.
