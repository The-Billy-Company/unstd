# Chaos campaign gates

These tests verify [chaos campaign tooling](../../../../tooling/chaos/README.md),
not product recovery contracts. They check deterministic schedules, bounded
journals, CLI admission, real worker exit/accounting, watchdogs, CPU/memory/output
limits, owned descendant cleanup, exact replay, and CI artifact retention.
`_campaign_fixtures.py` supplies reviewed child-only success and failure controls.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/chaos -q
```

These tests run under the default `gates` owner. Product fault experiments live
in [adversarial/chaos](../../adversarial/chaos/README.md) and require explicit
selection; some process-limit controls are platform-specific.
