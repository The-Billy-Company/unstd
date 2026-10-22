# Model contracts

Contracts for the strict Pydantic base classes in `unstd.model`: inherited configuration, aliases, frozen values, and settings behavior.

- [unit/](unit/README.md): validation, inheritance, serialization, and environment settings.

This module requires the `model` extra (`pydantic` and `pydantic-settings`). Collection omits its tests when the module cannot be imported.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/model -q
```

See [the module documentation](../../../../src/unstd/model/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
