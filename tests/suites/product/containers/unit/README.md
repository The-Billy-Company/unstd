# Sorted and persistent container contracts

`test_containers.py` checks exported names and useful missing-extra errors, then tests sorted-list/dict/set ordering and immutable-map persistence with deterministic generated histories. Derived maps must leave their predecessors unchanged.

Operations run when sortedcontainers or immutables is installed. Missing-backend construction cases and the explicit error-message contract cover the unavailable capability.

`test_missing_backends.py` blocks one backend at a time in a fresh interpreter, so the real import guards take their placeholder branch even where the extra is installed. Only the blocked family's flag and constructors degrade, with the exact actionable message; the other family still builds. Map remains the upstream class; accelerated sorted types remain subclasses of their upstream types.

`test_accelerated_sorted.py` compares accelerated collections with the upstream
implementations across block splits, positional indexes, keys, user subclasses,
copying, pickling and comparison failures. Blocking the native module or using
an unqualified backend version retains the original upstream implementations.

`test_lazy_exports.py` checks fresh-process package discovery, imports of each
family, cached class identity, star imports and reloads. Loading one family must
leave the other backend unloaded, and `dir()` must discover all public names
without loading either backend.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/containers/unit -q
```

See [the module test overview](../README.md) for related evidence.
