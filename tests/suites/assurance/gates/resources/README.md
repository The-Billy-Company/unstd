# Resource harness gates

`test_harness.py` checks [resource workers](../../../../support/resources/runner.py)
with real success, assertion, crash, output-flood, hang, memory-growth, leak, and
descendant-process controls. It requires accurate backend/provenance measurements,
bounded output, enforced wall/CPU/memory budgets, unconditional process-group
cleanup, and admission before launch. Resource containment is supported on Linux
and macOS; these gates skip on other platforms.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/resources -q
```

These harness checks run in ordinary pytest under `gates`. The product
[resource contracts](../../adversarial/resources/README.md) have a separate
explicitly selected lane.
