# TOML API contracts

`test_toml.py` compares reads, native values, binary-file requirements, and error identity with stdlib `tomllib`. It forces the unavailable-writer guard and, when tomlkit is present, checks writes, comment/layout preservation, and document/table/array helpers.

`test_style_edits.py` compares `parse` → edit → `dumps` with exact, hand-reviewed before/after documents. Only the edited lines may change. Comments, blank lines, key order, radix and literal-string spellings, and multi-line array layout must survive, and unedited awkward documents must re-emit byte for byte. `test_value_boundaries.py` reads hand-computed TOML 1.0.0 datetimes, offsets, truncated fractions, signed zero, NaN, integer limits and radixes. It also rejects out-of-range or misspelled values and round-trips writer boundaries through `tomllib`. `test_dialect_versions.py` holds TOML 1.1-only fixtures: the stdlib reader accepts them from Python 3.15 onward, and `parse` always reads and preserves them. The writer must spell ESC in plain strings as the 1.0 `\u001b`, never the 1.1-only `\e`.

Reading requires no product extra. The editable/writing surface requires the `toml` extra and must name that extra in its missing-capability error.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml/unit -q
```

See [the module test overview](../README.md) for related evidence.
