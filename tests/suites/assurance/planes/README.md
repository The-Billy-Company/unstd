# Integration and artifact planes

These directories group tests of real installation and application boundaries.

- [packaging](packaging/README.md) is **active**: offline wheel/sdist builds,
  archive integrity, pristine base and per-extra consumers, and downstream typing.
  Run `uv run --no-sync python -m tests.tooling run packaging -- -q` from the
  repository root after preparing its documented offline prerequisites.
- [integration](integration/README.md) is the **planned** `system-integration`
  lane for broader cross-module application scenarios. It contains no tests and
  cannot report a passing run.

Existing filesystem and process integration contracts remain in their owning
product suites; `uv run --no-sync python -m tests.tooling run integration -- -q`
selects those active tests. The [inventory](../../../tooling/suites/inventory.toml)
defines each lane's actual status and paths.
