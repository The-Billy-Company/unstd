# YAML contracts

Contracts for `unstd.yaml`: safe YAML 1.2 values, editable documents, lazy streams, and untrusted-input boundaries.

- [unit/](unit/README.md): safe/editable APIs, aliases, ownership, and missing-extra behavior.
- [security/](security/README.md): constructor refusal, duplicate keys, merge rules, and bounded parser probes.

YAML operations require the `yaml` extra (`ruamel.yaml`). The unit suite also verifies the actionable missing-extra error in an isolated base install.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml -q
```

See [the module documentation](../../../../src/unstd/yaml/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
