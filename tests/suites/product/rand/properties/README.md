# Randomness properties

`test_draw_laws.py` generates seeds, counts, ranges, weights, and counted populations for `Generator` streams on each backend. Interval bounds, inclusive/exclusive integer endpoints, zero-weight exclusion, sample sub-multisets, and shuffle permutations are the oracles; a same-seed twin must reproduce every draw, and a rejected interval must leave the stream where the twin expects it.

Stdlib cases need no extras. NumPy cases skip locally without the `rand` capability and fail under `--require-extras`. NumPy and stdlib sequences are never compared with each other.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rand/properties -q
```

See [the module test overview](../README.md) for related evidence.
