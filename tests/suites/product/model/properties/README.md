# Strict model properties

`test_config_parity.py` generates field layouts, including aliases that collide with other field names, and lax payloads with unknown keys. Each layout is built on the `unstd.model` base and on plain Pydantic with the documented configuration stated explicitly. Validation outcomes, error types, locations and inputs, both dump shapes, the fields-set, extras and JSON schemas must match. `FrozenModel` examples also compare hashing and assignment outcomes. `StrictSettings` examples are restricted to initializer input on both sides, so no environment or dotenv source is read.

The `model` extra is required. Shared collection policy omits this leaf when `unstd.model` cannot be imported; `--require-extras` makes missing declared capabilities an error. Hypothesis depth and example counts follow the shared test profiles.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/model/properties -q
```

See [the module test overview](../README.md) for related evidence.
