# TOML properties

`test_toml_invariants.py` generates TOML documents and compares text/binary reads with stdlib `tomllib`. It also checks writer output through `tomllib` when available and forces the missing-writer branch to verify an actionable failure.

Read laws run without extras; writer round trips require tomlkit from the `toml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml/properties -q
```

See [the module test overview](../README.md) for related evidence.
