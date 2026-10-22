# Sorted and persistent container contracts

`test_containers.py` checks exported names and useful missing-extra errors, then tests sorted-list/dict/set ordering and immutable-map persistence with deterministic generated histories. Derived maps must leave their predecessors unchanged.

Operations run when sortedcontainers or immutables is installed. Missing-backend construction cases and the explicit error-message contract cover the unavailable capability.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/containers/unit -q
```

See [the module test overview](../README.md) for related evidence.
