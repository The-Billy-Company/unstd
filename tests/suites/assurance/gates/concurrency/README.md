# Concurrency campaign gates

These tests verify [concurrency campaign tooling](../../../../tooling/concurrency/README.md):
inventory-derived group selection, explicit opt-in, bounded controls, CLI status,
source provenance, journal identity, replay, and CI coverage. Cross-lane controls
prevent chaos evidence or inherited pytest selectors from changing the requested
concurrency campaign. Replay checks the seeded schedule and result laws; actual
thread completion order may differ.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/concurrency -q
```

These gates participate in ordinary pytest. Product histories live in
[adversarial/concurrency](../../adversarial/concurrency/README.md), which needs
explicit selection. Shared worker containment is also checked by the chaos gates.
