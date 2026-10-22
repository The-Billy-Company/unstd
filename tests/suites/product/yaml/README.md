# YAML contracts

Contracts for `unstd.yaml`: safe YAML 1.2 values, editable documents, lazy streams, and untrusted-input boundaries.

- [unit/](unit/README.md): safe/editable APIs, aliases, ownership, YAML 1.2 specification examples, and missing-extra behavior.
- [properties/](properties/README.md): generated safe values, streams, and editable documents round-trip with exact types.
- [security/](security/README.md): constructor refusal, duplicate keys, merge rules, hostile resource corpus, and malformed bytes.
- [integration/](integration/README.md): stream faults, cleanup and ownership, and interleaved/threaded parser isolation.
- [regression/](regression/README.md): merge-key amplification and editable-writer value changes.

YAML operations require the `yaml` extra (`ruamel.yaml`). The unit suite also verifies the actionable missing-extra error in an isolated base install.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml -q
```

See [the module documentation](../../../../src/unstd/yaml/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
