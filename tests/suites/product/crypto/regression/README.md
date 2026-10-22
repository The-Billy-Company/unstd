# Cryptography regression reservation

This directory is reserved for minimized confirmed defects in `unstd.crypto`,
with an independent assertion and a reference to the original failure. It
currently contains no test files and is not an active inventory owner.

Follow [the coverage plan](../../../../COVERAGE.md) before adding tests, then
register this path in the existing active `regression` suite in
[the inventory](../../../../tooling/inventory.toml). No runnable coverage is
claimed here. Current evidence lives in [unit](../unit/README.md) and
[security](../security/README.md).
