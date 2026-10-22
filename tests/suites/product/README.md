# Product contracts

Tests mirror `src/unstd/<module>`, with evidence kinds beneath each module.
Active leaves include `unit`, `parity`, `properties`, `security`, `regression`,
and filesystem/process `integration`. Each module README describes its leaves
and optional capabilities. The empty crypto and serde regression leaves are
reserved locations and claim no executable coverage.

Run all product contracts from the repository root:

```console
uv run --no-sync pytest tests/suites/product -q
```

See [the test guide](../../README.md) for environment setup, optional-backend
collection policy, markers, and Hypothesis profiles. Native-specific tests need
built extensions; optional capability and platform guards still apply.

Existing mixed API files retain their assertions. New files should make the
evidence kind explicit. Register active leaves in
[the inventory](../../tooling/inventory.toml); every
test file must have exactly one active suite owner. Shared strategy construction
belongs under `tests/support`, while the module-specific law stays here.

See [the coverage plan](../../COVERAGE.md) before adding modules. No test stub
that always passes or skips counts as implementing a planned lane.
