# Structural cloning contracts

Contracts for `unstd.clone`: value and type fidelity, mutation isolation, copy protocols, and optional native traversal.

- [unit/](unit/README.md): API, class lifetime, and native ownership contracts.
- [properties/](properties/README.md): generated cloning and dataclass conversion laws.

Core contracts use the stdlib. Pydantic, msgspec, and compiled-extension cases require their respective capabilities and otherwise skip.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone -q
```

See [the module documentation](../../../../src/unstd/clone/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
