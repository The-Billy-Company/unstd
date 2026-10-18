# Benchmark tooling

The existing standalone instrument remains [`bench/`](../../../bench/README.md)
with its public `python -m bench` interface and baseline. Its correctness tests
live in `tests/suites/assurance/gates/bench`. Future cross-module performance
evidence belongs under `tests/suites/assurance/evals/performance`.

```console
uv run --no-sync python -m bench list
uv run --no-sync python -m bench verify
uv run --no-sync pytest -q -m perf
```

Run speed floors on a quiet worker with all required backends. Record environment
and noise; skipped backends cannot count as successful speed evidence.
The pytest command selects the existing wall-clock calibration checks for the
instrument. Their assertions remain intact; ordinary correctness runs exclude
them. These instrument checks do not complete the planned performance lane.
