# Benchmark tooling

The standalone instruments live in [`bench/`](../../../bench/README.md).
`python -m bench` retains its stdlib speedup comparisons; `python -m bench.speeds`
registers every public function with checked inputs and frozen latency ceilings.
Harness correctness tests live in `tests/suites/assurance/gates/bench`.
The active [performance lane](../../suites/assurance/evals/performance/README.md)
enforces the latency baseline on an explicitly selected quiet worker.

```console
uv run --no-sync python -m bench list
uv run --no-sync python -m bench verify
uv run --no-sync python -m bench.speeds check
uv run --no-sync python -m tests.tooling run performance -- -q
uv run --no-sync pytest -q -m perf
```

Run speed floors on a quiet worker with all required backends. Record environment
and noise; skipped backends cannot count as successful speed evidence.
The pytest command selects the latency gate and existing wall-clock calibration
checks. Ordinary correctness runs exclude them. Absolute ceilings require the
recorded environment; use `UNSTD_SPEED_BASELINE` to select an unchanged baseline
captured on another worker before comparing revisions.
