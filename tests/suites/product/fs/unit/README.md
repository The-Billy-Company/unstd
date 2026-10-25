# Filesystem API contracts

`test_fs.py` covers text/byte round trips, atomic-write rollback and scratch cleanup, permissions, symlink-aware file walking, directory helpers, and missing-path/error behavior on real temporary paths.
Directory checks include existing files and symlinks to directories, files, and
missing targets, preserving creation errors and existing contents.

[`test_native_dispatch.py`](test_native_dispatch.py) has 11 admission contracts.
Exact bytes and default UTF-8 text retain their public payload type and returned
Path. Absent or declined kernels, other codecs, subclasses, and replaced OS
bindings use the complete Python writer, including parent preparation and text
validation. Native I/O errors are never retried through Python. Binding checks
use identity so custom callable equality cannot execute or spoof admission.
These tests supply controlled backend objects and run without the extension;
real compiled dispatch is proved in [integration](../integration/README.md).

No product extras are needed. POSIX permission cases are guarded on Windows; deeper failure/interruption evidence lives in [integration](../integration/README.md) and confirmed defects in [regression](../regression/README.md).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/fs/unit -q
```

See [the module test overview](../README.md) for related evidence.
