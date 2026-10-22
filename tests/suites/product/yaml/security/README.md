# YAML input boundaries

`test_yaml_boundary.py` checks that safe/editable entry points refuse attacker-selected constructors and duplicate semantic keys, while valid merge/alias controls remain usable. Depth, encoding, and alias probes use bounded workers and avoid expanding alias graphs merely to inspect them.

`test_merge_duplicates.py` retains duplicate explicit-key cases hidden by merge keys, alongside valid precedence and explicit-override controls. These contracts require ruamel.yaml from the `yaml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml/security -q
```

See [the module test overview](../README.md) for related evidence.
