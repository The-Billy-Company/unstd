# Assurance contracts

Follow Billy's risk-owned assurance organization. `gates` tests the repository
and its quality instruments; `planes` proves real integration and artifact
boundaries; `security` holds behavioral penetration probes and distinct
structural canaries; `adversarial` applies faults, concurrency, resource pressure,
and committed-corpus replay; `evals/performance` measures speed and scaling.

A module-owned invariant stays under `suites/product/<module>/<kind>`. A shared
harness belongs under support; standalone campaigns belong under tooling.
The [inventory](../../tooling/inventory.toml) marks unimplemented lanes planned.
See [the coverage plan](../../COVERAGE.md) for activation criteria and priorities.
