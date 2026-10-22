# Settings source integration

`test_settings_sources.py` runs each scenario in a fresh deadline-owned worker whose environment holds only fixture-owned variables, with a fixture-written dotenv file in its own working directory. The developer's environment never reaches a result.

Expected values follow the pydantic-settings source order: initializer, environment, dotenv, then defaults. Scenarios cover case-insensitive names, validation aliases ignoring the prefix, initializer input by name or alias, nested groups merged across sources, invalid values, and a base without a configured dotenv file. An explicitly configured `BaseSettings` twin must agree on every scenario. A default-`forbid` control shows the ambient, dotenv and initializer extras the root `ignore` policy accepts, while nested `StrictModel` groups still reject unknown keys from every source.

The `model` extra is required. Shared collection policy omits this leaf when `unstd.model` cannot be imported; `--require-extras` makes missing declared capabilities an error.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/model/integration -q
```

See [the module test overview](../README.md) for related evidence.
