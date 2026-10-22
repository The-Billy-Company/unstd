# Iterator API contracts

`test_iters.py` checks lazy consumption with spies, bounded generators, and infinite iterators. Cases include uneven/strict chunking, windows and take/drop operations, unhashable uniqueness keys, single-source partitioning, and empty/default edges. Adversarial cases also pin
mutable predicate verdicts, exactly-once truth tests, cross-hashability equality,
and propagation of comparison errors.

`test_consumption_faults.py` pins hand-derived size/stride tables (0, 1, n-1,
n, n+1, negative, step larger than the window or input), validation before the
source is touched, and key/predicate faults that propagate with identity after
the exact reference prefix. Partition sides drain in either order or
interleaved; one untouched side buffers every item, as documented, while
in-step pulls hold a length-independent buffer. Weakly referenced tokens show
that dedup retains only first occurrences (or only keys), and that windows,
chunks, runs and draining selectors do not retain consumed items.

The combinators use only the stdlib and require no product extras.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/iters/unit -q
```

See [the module test overview](../README.md) for related evidence.
