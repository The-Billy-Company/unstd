# Strict model and settings contracts

`test_model.py` checks Pydantic configuration inheritance, including multiple inheritance, name/alias input, exact unknown-field and frozen errors, frozen/hashable values, and the settings-specific extra-field policy.

`test_contracts.py` compares the bases with plain Pydantic models carrying the documented configuration explicitly. It pins the exact configuration dictionaries, that `StrictModel` is not Pydantic strict mode, and that the deliberately unset options keep Pydantic's defaults. Nested trees must report every unknown field at its exact location while a nested plain model keeps its own policy; subclass overrides inherit. Alias cases cover alias-plus-name input, an alias naming another field, alias choices and serialization aliases. Schemas must equal the explicit twins in both modes, frozen models must reject assignment and deletion and raise the twin's `TypeError` when holding unhashable values, and settings validation is restricted to initializer input.

`test_backend_errors.py` blocks `pydantic` or `pydantic-settings` in fresh workers and requires the documented ImportError naming the `model` extra from every entry point, chained to the original import failure.

The `model` extra is required. Shared collection policy omits this leaf when `unstd.model` cannot be imported; `--require-extras` makes missing declared capabilities an error.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/model/unit -q
```

See [the module test overview](../README.md) for related evidence.
