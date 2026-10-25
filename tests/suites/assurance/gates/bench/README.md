# Benchmark evidence gates

These tests verify the [benchmark harness](../../../../../bench/README.md) and
its comparison tools: timing calibration and collector restoration, equivalence
audits, baseline floors, source isolation, workload coverage, paired timing, and
observed native dispatch. Deliberately wrong outputs and declined native calls
must fail before they can support an acceleration claim.

`test_gzip_reads.py` rejects changed decoded bytes and incorrectly imported
comparison sources, and checks bounded output, concatenated lines and rewinds.

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
`test_speeds_workflow.py` checks isolated revision builds and shared-worker
comparison policy. These checks have no machine-speed assertions.

The common clone guard requires successful native graph, dataclass and `asdict`
calls in the candidate. A historical baseline with structural copying must prove
its native builtin-tree route and independent alias copies; newer shared-graph
baselines retain all three native proofs. Every revision still checks and times
all 25 workloads with GC enabled and the same wall/CPU limits.
Its unit tests reject declined native calls and separate wall/CPU regressions,
check baseline entrypoint isolation, and require evidence to survive failure.

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

The rand backend and consumer gates compare source bytes, public signatures,
native bindings, exact outputs and complete RNG states outside timing. Keyed
and sparse-sampling gates separately validate explicit stream/strategy changes,
source receipts, input laws and report retention when a requested speedup fails.
The measured 100× scope is an 8,000-key count phase, not a complete application
workflow. See the [rand evidence](../../../../../bench/reports/rand/README.md).
