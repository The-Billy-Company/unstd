# TOML properties

`test_toml_invariants.py` generates TOML documents and compares text/binary reads with stdlib `tomllib`. It also checks writer output through `tomllib` when available and forces the missing-writer branch to verify an actionable failure.

`test_toml_edit_laws.py` writes generated aware/naive datetimes, dates, times, 64-bit integers, floats and Unicode keys/strings within the common read/write dialect: minute-precision offsets, offset-free times, and no ESC character. `tomllib` must read them back with exact values and offsets, and `parse` must re-emit written text unchanged. A `RuleBasedStateMachine` applies set/delete edits to a parsed document; after every step the document must match a plain-dict model and keep its header comment.

Read laws run without extras; writer round trips require tomlkit from the `toml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml/properties -q
```

See [the module test overview](../README.md) for related evidence.
