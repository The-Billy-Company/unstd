# Benchmark evidence gates

These tests verify the [benchmark harness](../../../../../bench/README.md) and
its comparison tools: timing calibration and collector restoration, equivalence
audits, baseline floors, source isolation, workload coverage, paired timing, and
observed native dispatch. Deliberately wrong outputs and declined native calls
must fail before they can support an acceleration claim.

`test_crypto_hash.py` verifies saved crypto helper isolation, missing-helper
failures, source hashes despite stale bytecode, crossover workload coverage,
output/type checks, and retained alternating samples.

From the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/bench -q
uv run --no-sync pytest tests/suites/assurance/gates/bench -q -m perf
```

The second command selects three timer calibration checks excluded by default;
use a quiet machine. The directory is omitted from optional collection when
`bench` cannot import; `--require-extras` makes missing required capabilities
fatal. These are harness checks; the separate product performance lane remains
planned.
