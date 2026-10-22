# Model contracts

Contracts for the strict Pydantic base classes in `unstd.model`: inherited configuration, aliases, frozen values, and settings behavior.

- [unit/](unit/README.md): validation, inheritance, aliases, schemas, frozen values, settings validation, and missing-extra errors.
- [properties/](properties/README.md): generated layouts and payloads against explicitly configured Pydantic twins.
- [integration/](integration/README.md): environment and dotenv precedence in isolated workers.

This module requires the `model` extra (`pydantic` and `pydantic-settings`). Collection omits its tests when the module cannot be imported.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/model -q
```

See [the module documentation](../../../../src/unstd/model/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
