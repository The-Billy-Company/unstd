---
doc_radar:
  sentinels:
    - file: test_paths.py
      contains: ['def test_ensure_dir_rechecks_a_previously_created_path(', 'def test_repeated_replace_publishes_each_new_source_before_returning(', 'def test_direct_directory_policy_matches_path_mkdir_state_and_errors(']
---

# Filesystem API Contracts

`test_fs.py` covers text/byte round trips, atomic-write rollback and scratch cleanup, permissions, symlink-aware file walking, directory helpers, and missing-path/error behavior on real temporary paths.
Directory checks include existing files and symlinks to directories, files, and
missing targets, preserving creation errors and existing contents.

`test_paths.py` checks path normalization, coercion order and failures before
replacement. Directory checks revisit removed or replaced paths; repeated
renames publish each new source inode before returning, including equal bytes.
Path subclasses normalize to an ordinary platform Path before I/O.

Bounded audit-hook subprocesses pin both directory policies. The default keeps
existing directories as no-ops; `check_first=False` retains the ordinary
`Path.mkdir` attempt and its audit policy.

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
