# Strict model and settings contracts

`test_model.py` checks Pydantic configuration inheritance, including multiple inheritance, name/alias input, validation and serialization behavior, frozen/hashable values, and the settings-specific extra-field policy and environment parsing.

The `model` extra is required. Shared collection policy omits this leaf when `unstd.model` cannot be imported; `--require-extras` makes missing declared capabilities an error.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/model/unit -q
```

See [the module test overview](../README.md) for related evidence.
