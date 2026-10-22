# Fuzz tooling gates

These tests verify the [fuzz tools](../../../../tooling/fuzz/README.md): registry
and seed-manifest authentication, target oracles, deterministic mutation, CLI
routing, replay, worker limits, crash/hang accounting, reproduction artifacts,
and CI campaign configuration. Wrong product results and unexpected exceptions
must fail the target's laws. `_execution_fixtures.py` provides reviewed subprocess
controls for worker failures.

`test_process_ownership.py` observes real kernel waitability at both supervisors'
EOF cleanup boundary. Exited and signaled leaders stay waitable until group
cleanup, while a bounded timeout retains a running child for cleanup. Darwin
tests exercise both native observation and the CPython 3.12 libc backport,
including ABI layout and errno preservation. Repeated real replay, startup
failure and missing-backend runs overlap under a three-worker load; expected
startup failures remain findings rather than cleanup exceptions or successes.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/fuzz -q
```

These finite checks belong to the default `gates` owner. Committed corpus replay
has its own [adversarial suite](../../adversarial/fuzz_replay/README.md);
sustained mutation uses the standalone campaign CLI. Optional-backend tests
report capability skips locally; `--require-extras` requires every declared extra.
