# Randomness regressions

`test_weight_totals.py` pins weighted draws whose total is zero or negative: the NumPy path normalized by that total, turning all-negative weights into valid probabilities. Both engines must raise `ValueError` and leave the stream where a same-seed twin expects it.

NumPy cases skip locally without the `rand` capability and fail under `--require-extras`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rand/regression -q
```

See [the module test overview](../README.md) for related evidence.
