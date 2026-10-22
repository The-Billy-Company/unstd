# System integration

Status: **planned** (`system-integration`). This directory reserves cross-module
application scenarios beyond the active product filesystem/process integration
contracts. Clean wheel/sdist builds and installed consumers already belong to
the active [packaging plane](../packaging/README.md).

No executable coverage is claimed by this directory. Follow
[the coverage plan](../../../../COVERAGE.md) before adding tests, then activate this lane in
`tests/tooling/inventory.toml`. A required lane must fail when its prerequisites
are absent. Use success controls, explicit bounds, and independent expectations.
