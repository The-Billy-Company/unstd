# YAML API contracts

`test_yaml.py` checks safe YAML 1.2 values, duplicate/tag rejection, aliases and recursive identity, editable comment/quote/anchor preservation, lazy multi-document reads, streamed writes, and caller-owned handle lifetimes. An isolated base-install probe checks every capability names its required extra.

Data operations require ruamel.yaml from the `yaml` extra. Tests stay collectable without it; the base-install error probe still runs.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml/unit -q
```

See [the module test overview](../README.md) for related evidence.
