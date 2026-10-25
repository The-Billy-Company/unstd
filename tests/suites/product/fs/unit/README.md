# Filesystem API contracts

`test_fs.py` covers text/byte round trips, atomic-write rollback and scratch cleanup, permissions, symlink-aware file walking, directory helpers, and missing-path/error behavior on real temporary paths.
Directory checks include existing files and symlinks to directories, files, and
missing targets, preserving creation errors and existing contents.

No product extras are needed. POSIX permission cases are guarded on Windows; deeper failure/interruption evidence lives in [integration](../integration/README.md) and confirmed defects in [regression](../regression/README.md).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/fs/unit -q
```

See [the module test overview](../README.md) for related evidence.
