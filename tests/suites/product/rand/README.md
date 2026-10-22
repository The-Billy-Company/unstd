# Randomness contracts

Contracts for `unstd.rand`: secure token helpers and seedable scalar/bulk pseudo-random draws.

- [unit/](unit/README.md): bounds, token shapes, deterministic seeding, and fallback values.

The suite forces the stdlib draw backend and exercises NumPy when available. Reproducibility is checked within each backend; their sequences need not match.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/rand -q
```

See [the module documentation](../../../../src/unstd/rand/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
