# TOML API contracts

`test_toml.py` compares reads, native values, binary-file requirements, and error identity with stdlib `tomllib`. It forces the unavailable-writer guard and, when tomlkit is present, checks writes, comment/layout preservation, and document/table/array helpers.

Reading requires no product extra. The editable/writing surface requires the `toml` extra and must name that extra in its missing-capability error.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml/unit -q
```

See [the module test overview](../README.md) for related evidence.
