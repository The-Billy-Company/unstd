# Structural cloning contracts

Contracts for `unstd.clone`: value and type fidelity, mutation isolation, copy protocols, and optional native traversal.

- [unit/](unit/README.md): API, class admission and lifetime, memo and keep-alive ordering, copy-hook failures, recursion-limited workers, and native ownership contracts.
- [properties/](properties/README.md): generated mixed-tree, model/struct, native agreement, and dataclass conversion laws.

Core contracts use the stdlib. Pydantic and msgspec cases skip locally when absent and fail under `--require-extras`; compiled-extension cases skip when the extension is not built.

Direct mixed-graph and record acceleration assertions use the
`native_mixed_graph` fixture from `conftest.py` on their qualified CPython
3.12–3.14 ABIs. Python 3.15 runs all public cloning contracts and explicit
native-refusal/fallback checks, retaining the Python walker for those paths.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone -q
```

See [the module documentation](../../../../src/unstd/clone/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
