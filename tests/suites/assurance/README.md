# Assurance contracts

Assurance suites group cross-cutting risks by owner:

- [gates](gates/README.md) checks the repository, release policy, and test tools.
- [planes](planes/README.md) proves installed artifact boundaries and reserves
  broader system integration scenarios.
- [security](security/README.md) holds TLS penetration probes and primitive canaries.
- [adversarial](adversarial/README.md) applies faults, concurrency, resource
  pressure, and committed-corpus replay.
- [evals](evals/README.md) reserves performance and scaling evidence; that lane
  remains planned.

A module-owned invariant stays under `suites/product/<module>/<kind>`. A shared
harness belongs under support; standalone campaigns belong under tooling.
The [inventory](../../tooling/inventory.toml) marks unimplemented lanes planned.
See [the coverage plan](../../COVERAGE.md) for activation criteria and priorities.
