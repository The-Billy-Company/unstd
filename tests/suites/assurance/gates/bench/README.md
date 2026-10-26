# Benchmark evidence gates

These tests verify the [benchmark harness](../../../../../bench/README.md) and
its comparison tools: timing calibration and collector restoration, equivalence
audits, baseline floors, source isolation, workload coverage, paired timing, and
observed native dispatch. Deliberately wrong outputs and declined native calls
must fail before they can support an acceleration claim.

The serde profiler resolves encoder caches in the selected current or historical
source layout. A missing layout may select an older location; a broken encoder
or missing dependency must remain an error rather than borrowing another codec.

SortedList operation variants use stdlib bisect positions and an independently
sorted insertion model. Their fresh-list fixture cannot accumulate state across
batches; a deliberately disabled insertion fails its output oracle. Additive
capture checks reject incomplete provenance and incompatible selected runtimes
while preserving every existing ceiling and historical capture record.

`test_fs_backend.py` checks saved/current source isolation, stale-bytecode
rejection, unchanged signatures, independent output checks, shared calibrated
batch counts, alternating sample order, and repeatable filesystem fixtures.
Its checks use no machine-speed thresholds.

[`test_billy_fs.py`](test_billy_fs.py) pins consumer-source and payload receipts,
synthetic-data boundaries, byte-oriented browser writes, retained quota checks,
shared paths, independent content/mode checks, and native-entrypoint evidence.
Untimed sentinel resets prevent a no-op native call from borrowing a preceding
Python result.

[`test_billy_fs_installed.py`](test_billy_fs_installed.py) gates the production
[`billy_fs_installed.py`](../../../../../bench/profiles/billy_fs_installed.py)
driver. Both independent public fs modules come from one selected installation;
one retains native dispatch and the other forces Python fallback. The gates
reject missing/noncompiled or ambient binaries, shared module state, inactive or
declined native routes, no-op results, and wheel members that differ from the
loaded fs sources/binary. Public text inputs stay text at dispatch. Support
modules need file provenance and direct `--help` works without site packages.
The driver proves each route and checks fresh sentinel/output state outside
timing, preserving profile hooks on failure. The retained [production report](../../../../../bench/reports/fs/production/README.md)
covers all 13 Billy shapes through direct and sdist-rebuilt Linux wheels, with
disk and tmpfs results kept separate. These gates impose no machine-speed
threshold and the test-only ELF observer is excluded from profiling.

`test_jsonx_throughput.py` rejects missing/wrong installed native extensions,
inactive bindings, incorrect native results, source changes and empty workload
filters. It verifies alternating installed-root comparisons, finite slowdown
limits, and evidence retention when a wall-time limit fails, without measuring
machine speed in the correctness suite.
Its child-bootstrap regression disables site packages and supplies the product
only through the selected installation root, matching CI's dependency-only
environment and separate `--target` installations.
Drifting-worker fixtures require matched repetition ratios and prove that a
persistent wall-time regression still fails the same limit.
`test_jsonx_pairing.py` checks adjacent batch order and equal iteration budgets,
two real native installations with independent globals and bindings, complete
module/path restoration on success and failure, and provenance drift on either
side of timing.

`test_gzip_reads.py` rejects changed decoded bytes and incorrectly imported
comparison sources, and checks bounded output, concatenated lines and rewinds.
The gzip consumer gates check the exact HTTP sentinel budget, upload chunk sizes
and position restoration, full-query EOF, per-entry journal writes and batch
flushes, appended members, and fixture/output corruption rejection.
It validates all 14 committed synthetic consumer shapes, rejects nonfinite or
unpaired slowdown limits, and ensures faster CPU samples cannot hide a wall-time
regression. The wall gate uses the median of matched repetition ratios and keeps
both independent latency medians and each original pair. Drifting-worker
controls distinguish a mismatched median from a persistent slowdown above the
unchanged 30% limit.
`test_gzip_concurrency.py` checks isolated direct workers, bounded concurrency,
alternating samples, native codec proof, validation failures and collector
restoration, and the complete-worker byte denominator including both upload
passes. It rejects comparisons with different frozen inputs and uses no
wall-time thresholds.

The gzip request gates check exact checkpoint/full compressed-byte
identity, bounded prefix ownership and retirement, original fixture bytes,
level/prefix misses, and the threshold/shrink gate. It proves that a contract
failure remains ineligible while other cases can still be measured, and that
the final report is retained before failure. It has no machine-speed assertions.

`test_crypto_hash.py` verifies saved crypto helper isolation, missing-helper
failures, source hashes despite stale bytecode, crossover workload coverage,
output/type checks, and retained alternating samples.

The audio consumer gates reject changed PCM/WAV bytes, post-measurement drift,
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
the version-specific C fast paths. The gzip consumer workflow gate also pins the
direct entry point, isolated installations, shared benchmark dependencies,
committed fixtures, 30% wall-time limit and retained report. These tests have no
machine-speed assertions.

The common clone guard requires successful native graph, dataclass and `asdict`
calls in the candidate. A historical baseline with structural copying must prove
its native builtin-tree route and independent alias copies; newer shared-graph
baselines retain all three native proofs. Every revision still checks and times
all 30 workloads with GC enabled and the same wall/CPU limits.
Its unit tests reject declined native calls and separate wall/CPU regressions,
check baseline entrypoint isolation, and require evidence to survive failure.
Matched-round ratios reject persistent slowdowns while tolerating common worker
drift; unpaired or nonfinite measured samples fail qualification.

`test_proc_backend.py` loads the process profile directly without optional
product dependencies. It rejects incorrect outputs and changed signatures,
checks source fingerprints and isolated imports, and verifies GC restoration
after a failed measured call. Timing ratios remain evidence rather than unit
test thresholds.

`test_timing_noise.py` uses virtual clocks to reject preempted calibration
batches and retain separate wall/CPU evidence. `test_rand_diagnose.py` exercises
alternating direct controls, source identities, cleanup and baseline overwrite
rejection. Candidate-only delays remain failures; an equally slow control cannot
convert a candidate above its frozen ceiling to a pass.

The rand backend and consumer gates compare source bytes, public signatures,
native bindings, exact outputs and complete RNG states outside timing. Keyed
and sparse-sampling gates separately validate explicit stream/strategy changes,
source receipts, input laws and report retention when a requested speedup fails.
The measured 100× scope is an 8,000-key count phase, not a complete application
workflow. See the [rand evidence](../../../../../bench/reports/rand/README.md).

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
