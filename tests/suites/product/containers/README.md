# Container contracts

Contracts for `unstd.containers`: sorted collections, persistent maps, and actionable errors when optional implementations are absent.

- [unit/](unit/README.md): ordering, persistence, public names, compatible accelerated sorted types, and missing-backend behavior in isolated interpreters.
- [properties/](properties/README.md): sorted-collection and persistent-map state machines against list/bisect, dict and set models, including comparison/hash faults and retained-version isolation.

Backend operations require the `containers` extra (`sortedcontainers` and `immutables`). Missing-backend guards remain testable without it.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/containers -q
```

See [the module documentation](../../../../src/unstd/containers/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
