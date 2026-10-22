# Iterator API contracts

`test_iters.py` checks lazy consumption with spies, bounded generators, and infinite iterators. Cases include uneven/strict chunking, windows and take/drop operations, unhashable uniqueness keys, single-source partitioning, and empty/default edges.

The combinators use only the stdlib and require no product extras.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/iters/unit -q
```

See [the module test overview](../README.md) for related evidence.
