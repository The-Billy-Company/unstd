# YAML properties

`test_yaml_values.py` generates YAML 1.2 safe values — null, booleans, unbounded integers, floats including infinities, NaN and `-0.0`, and strings including resolver-ambiguous words (`yes`, `null`, `~`, `0o17`, `2024-01-01`), indicators, edge whitespace, line breaks, NEL and non-ASCII — nested in lists and string-keyed mappings. Values must survive `dumps` → `loads`/`load` with exact types, multi-document streams must keep document order and count, and editable documents must keep both parsed and newly assigned values through `dumps`. A plain ruamel safe loader with the same pure-Python options reads every emitted text as an independent wrapper check.

These properties require ruamel.yaml from the `yaml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml/properties -q
```

See [the module test overview](../README.md) for related evidence.
