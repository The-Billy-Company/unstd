# Dependency trust and inventory tooling

[`policy.py`](policy.py) checks committed resolution inputs offline, authenticates
vendored source closures, and exports deterministic CycloneDX inventory. It also
rejects missing, malformed, incomplete, or non-clean saved `uv audit` evidence.
It does not resolve dependencies or contact an index during offline checks.

```console
python -m tests.tooling.supply_chain check --json
python -m tests.tooling.supply_chain sbom --help
python -m tests.tooling.supply_chain audit-report --help
```

[Supply-chain gates](../../suites/assurance/gates/supply_chain/README.md) own
policy controls and publication wiring. [Release verification](../release/README.md)
authenticates the final artifact handoff.
