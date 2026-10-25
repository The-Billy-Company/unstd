# Benchmark evidence gates

These tests verify the [benchmark harness](../../../../../bench/README.md) and
its comparison tools: timing calibration and collector restoration, equivalence
audits, baseline floors, source isolation, workload coverage, paired timing, and
observed native dispatch. Deliberately wrong outputs and declined native calls
must fail before they can support an acceleration claim.

`test_jsonx_throughput.py` rejects missing/wrong installed native extensions,
inactive bindings, incorrect native results, source changes and empty workload
filters. It verifies alternating installed-root comparisons, finite slowdown
limits, and evidence retention when a wall-time limit fails, without measuring
machine speed in the correctness suite.

`test_gzip_reads.py` rejects changed decoded bytes and incorrectly imported
comparison sources, and checks bounded output, concatenated lines and rewinds.
`test_billy_gzip.py` checks Billy's exact HTTP sentinel budget, upload chunk sizes
and position restoration, full-query EOF, per-entry journal writes and batch
flushes, appended members, and fixture/output corruption rejection.
It validates all 14 committed synthetic consumer shapes, rejects nonfinite or
unpaired slowdown limits, and ensures faster CPU samples cannot hide a wall-time
regression.
`test_gzip_concurrency.py` checks isolated direct workers, bounded concurrency,
alternating samples, native codec proof, validation failures and collector
restoration, and the complete-worker byte denominator including both upload
passes. It rejects comparisons with different frozen inputs and uses no
wall-time thresholds.

`test_crypto_hash.py` verifies saved crypto helper isolation, missing-helper
failures, source hashes despite stale bytecode, crossover workload coverage,
output/type checks, and retained alternating samples.

`test_billy_audio.py` rejects changed PCM/WAV bytes, post-measurement drift,
borrowed/read-only sample storage, missing/ambiguous consumer receipts and
source changes during timing. It checks compact CLI evidence without speed
thresholds.

`test_speeds.py` pins slowdown verdicts, frozen ceilings, malformed evidence
rejection and correctness audits before/after timing. `test_speed_coverage.py`
discovers every public operation, requires all-package workloads and frozen
rows, checks real workload oracles and proves fixture cleanup/state restoration.
`test_speeds_workflow.py` checks isolated revision builds, native-source triggers,
independent Linux/macOS jobs and evidence on Python 3.13 and 3.14, and each
platform/interpreter pair's shared-worker comparison policy. Python 3.14 covers
the version-specific C fast paths. The Billy gzip workflow gate also pins the
direct entry point, isolated installations, shared benchmark dependencies,
committed fixtures, 30% wall-time limit and retained report. These tests have no
machine-speed assertions.

`test_timing_noise.py` uses virtual clocks to reject preempted calibration
batches and retain separate wall/CPU evidence. `test_rand_diagnose.py` exercises
alternating direct controls, source identities, cleanup and baseline overwrite
rejection. Candidate-only delays remain failures; an equally slow control cannot
convert a candidate above its frozen ceiling to a pass.

From the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/bench -q
uv run --no-sync pytest tests/suites/assurance/gates/bench -q -m perf
```

The second command selects three timer calibration checks excluded by default;
use a quiet machine. The directory is omitted from optional collection when
`bench` cannot import; `--require-extras` makes missing required capabilities
fatal. The separate [performance lane](../../evals/performance/README.md) is
active and opt-in; it verifies the frozen per-function latency ceilings.

The measurement divisor is checked against an independent virtual nanosecond
clock at four known costs, so scheduler delays cannot masquerade as arithmetic
errors. The explicit `perf` tests still exercise real-clock calibration and
human-readable timing units.
