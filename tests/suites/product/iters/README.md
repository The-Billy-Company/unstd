# Iterator contracts

Contracts for the pure-stdlib `unstd.iters` combinators, including lazy consumption, uneven chunks, unhashable uniqueness, and empty/default behavior.

- [unit/](unit/README.md): iterator results, consumption histories, size/stride boundaries, key faults, uneven partition pulls, retained-object counts, and error edges.
- [properties/](properties/README.md): generated laws against plain-loop reference specifications, failing sources and exact consumption counts.
- [regression/](regression/README.md): default-step `windowed` reading its input before the first pull.

No optional product dependencies are required.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/iters -q
```

See [the module documentation](../../../../src/unstd/iters/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
