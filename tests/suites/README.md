# Executable test suites

Pytest discovers tests beneath this directory. [Product suites](product/README.md)
own contracts for individual `unstd` modules; [assurance suites](assurance/README.md)
own cross-module risks, installed artifacts, and test-tool correctness.

Every test file must have exactly one active owner in the
[suite inventory](../tooling/inventory.toml). Add new tests to the appropriate
owner and update the inventory when adding a new leaf. Planned lanes contain
documentation only and cannot be run as passing coverage.

From the repository root:

```console
uv run --no-sync python -m tests.tooling check
uv run --no-sync pytest -q
```

The default run excludes performance, chaos, concurrency, and resource lanes.
See the [test runbook](../README.md) for explicit selection and backend policy.
