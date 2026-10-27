# Filesystem contracts

Contracts for `unstd.fs`: atomic file replacement, cleanup, directory helpers, and symlink-aware traversal.

- [unit/](unit/README.md): public filesystem API, native admission, and Python fallback.
- [integration/](integration/README.md): real OS/stream faults, Linux native cancellation/error parity, writer interruption, and reader atomicity.
- [regression/](regression/README.md): scratch-file permissions and traversal race defects.

These tests use fixture-owned files and require no product extras. POSIX permission, descriptor, and symlink cases carry platform guards.
The optional Linux extension is exercised through public `fs.atomic_write`,
with a separately forced Python path for comparison. Its 62 integration cases
require Linux and an available native backend; the syscall-boundary cases also
compile the test-only [ELF observer](../../../../native/fs/qualification/README.md).
Missing compiler support skips only those observer-dependent cases. Native
release-wheel preflight requires the extension on Linux before the suite runs.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/fs -q
```

See [the module documentation](../../../../src/unstd/fs/README.md) for the public contract,
the [production qualification](../../../../bench/reports/fs/production/README.md)
for installed-wheel and Consumer workload evidence, and [the test guide](../../../README.md)
for environment setup.
