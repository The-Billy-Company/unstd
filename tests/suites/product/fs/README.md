# Filesystem contracts

Contracts for `unstd.fs`: atomic file replacement, cleanup, directory helpers, and symlink-aware traversal.

- [unit/](unit/README.md): public filesystem API and ordinary failure behavior.
- [integration/](integration/README.md): real OS/stream faults, writer interruption, and reader atomicity.
- [regression/](regression/README.md): scratch-file permissions and traversal race defects.

These tests use fixture-owned files and require no product extras. POSIX permission, descriptor, and symlink cases carry platform guards.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/fs -q
```

See [the module documentation](../../../../src/unstd/fs/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
