# Fuzz tooling gates

These tests verify the [fuzz tools](../../../../tooling/fuzz/README.md): registry
and seed-manifest authentication, target oracles, deterministic mutation, CLI
routing, replay, worker limits, crash/hang accounting, reproduction artifacts,
and CI campaign configuration. Wrong product results and unexpected exceptions
must fail the target's laws. `_execution_fixtures.py` provides reviewed subprocess
controls for worker failures.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/fuzz -q
```

These finite checks belong to the default `gates` owner. Committed corpus replay
has its own [adversarial suite](../../adversarial/fuzz_replay/README.md);
sustained mutation uses the standalone campaign CLI. Optional-backend tests
report capability skips locally; `--require-extras` requires every declared extra.
