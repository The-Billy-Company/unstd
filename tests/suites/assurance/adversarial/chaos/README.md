# chaos

Status: **planned**. Bounded syscall, stream, clock, entropy and killed-worker fault injection.

No executable coverage is claimed by this directory. Follow
[the coverage plan](../../../../COVERAGE.md) before adding tests, then activate this lane in
`tests/tooling/inventory.toml`. A required lane must fail when its prerequisites
are absent. Use success controls, explicit bounds, and independent expectations.
