# Iterator contracts

Contracts for the pure-stdlib `unstd.iters` combinators, including lazy consumption, uneven chunks, unhashable uniqueness, and empty/default behavior.

- [unit/](unit/README.md): iterator results, consumption histories, and error edges.

No optional product dependencies are required.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/iters -q
```

See [the module documentation](../../../../src/unstd/iters/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
