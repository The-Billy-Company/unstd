# Container contracts

Contracts for `unstd.containers`: sorted collections, persistent maps, and actionable errors when optional implementations are absent.

- [unit/](unit/README.md): ordering, persistence, public names, and missing-backend behavior.

Backend operations require the `containers` extra (`sortedcontainers` and `immutables`). Missing-backend guards remain testable without it.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/containers -q
```

See [the module documentation](../../../../src/unstd/containers/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
