# Performance

Status: **active, opt-in**. Every public callable has a checked workload in the
[speed registry](../../../../../bench/README.md). This lane verifies that its
latency stays within the frozen ceiling for the recorded worker environment.
It requires all extras and the baseline's interpreter, CPU, backend versions,
and native extension availability. Missing or incomparable evidence fails.

```console
uv sync --locked --all-extras --group dev
uv run --no-sync python -m tests.tooling run performance -- -q
```

The default baseline is `bench/baselines/speeds.json`. To compare revisions on another
quiet worker, freeze a separate baseline on the revision under comparison,
retain it unchanged, and select it for the candidate run:

```console
uv run --no-sync python -m bench.speeds freeze --baseline /tmp/unstd-speeds.json
UNSTD_SPEED_BASELINE=/tmp/unstd-speeds.json uv run --no-sync python -m tests.tooling run performance -- -q
```

Do not regenerate that baseline after introducing a slowdown. Coverage and
correctness checks run before timing; newly exported APIs require registered
workloads and frozen rows. The ordinary correctness run excludes `perf`.
`pytest -m perf` selects this lane and the existing timer calibration checks.
The frozen inputs measure bounded representative operations; scaling and soak
claims require additional workloads and controlled workers.
