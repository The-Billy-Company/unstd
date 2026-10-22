# TOML regressions

Status: **active**. `test_writer_dialect.py` pins `dumps`/`dump` to TOML 1.0,
the dialect stdlib `tomllib` reads. tomlkit 0.15 spelled ESC in plain strings
and keys as the 1.1-only `\e`. It also wrote seconds- or microsecond-precision
offsets (`+05:30:15`) and offset-carrying local times (`07:32:00+00:00`), which
no TOML version accepts. Expected text is hand-written from the TOML 1.0
grammar: `\u001b` for ESC, `HH:MM` or `Z` for offsets, and bare local times.
`tomllib` reads each document back.

The module also pins the boundaries of the fix:
- Unrepresentable values raise `ValueError` at any depth before `dump` writes
  a byte.
- Values assigned into a parsed document are checked too.
- Parsed `\e` spellings re-emit byte for byte.
- Whole-minute offsets, naive times, and dates still write unchanged.

Requires the `toml` extra (`tomlkit`).

Run `uv run --no-sync pytest tests/suites/product/toml/regression -q`.

See [the module test overview](../README.md) for related evidence.
