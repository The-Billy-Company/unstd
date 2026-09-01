# `unstd.fs` — filesystem operations `pathlib`/`os` lack

Part of [`unstd`](../README.md). **Pure stdlib — no optional extra.**
`pathlib` is best-in-class path _algebra_ (joins, suffixes, parents); it stops at
the durable-write and fast-walk _operations_ every service otherwise re-rolls.
`unstd.fs` is exactly those operations — it is **not** a `pathlib` replacement.
Keep `Path` for path math; reach here for the I/O around it. Every function
returns / yields `pathlib.Path` for interop with the rest of the codebase.

```python
from unstd import fs
```

## Surface

| Function                                                                  | Does                                                                                                                 |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `atomic_write(path, data, *, encoding="utf-8", durable=False)`            | Crash-safe write of `str`/`bytes`: temp file in the same dir → `fsync` → `os.replace`.                               |
| `atomic_writer(path, *, mode="w"\|"wb", encoding="utf-8", durable=False)` | Context-manager form — yields a handle; commits atomically on clean exit, discards the temp file if the body raises. |
| `atomic_replace(src, dest, *, durable=False)`                             | Atomic `os.replace` of an existing file onto `dest` (same-filesystem rename).                                        |
| `read_bytes(path)` / `read_text(path, *, encoding="utf-8")`               | Sized one-shot reads (`fstat` length → single `read`).                                                               |
| `ensure_dir(path)`                                                        | `mkdir(parents=True, exist_ok=True)`, idempotent.                                                                    |
| `sync_dir(path)`                                                          | `fsync` a directory handle so a create/rename inside it is durable.                                                  |
| `iter_dir(path)`                                                          | `os.scandir` generator — immediate entries, one level.                                                               |
| `walk_files(root, *, follow_symlinks=False)`                              | `os.scandir` generator — every file, recursively.                                                                    |

## The atomic-write idiom

`atomic_write` (and its `atomic_writer` context manager) implement the
durable-write pattern `pathlib` has no equivalent for:

1. Create a temp file **in the destination's own directory** (so the final
   rename stays on one filesystem — `os.replace` is only atomic within a
   filesystem).
2. Write the payload, `flush()`, then `os.fsync()` the file descriptor.
3. `os.replace(tmp, dest)` — an atomic same-filesystem rename. A reader ever
   sees the whole old file or the whole new file, never a torn half-write.

If the body raises, the temp file is removed and the destination is left exactly
as it was. Pass `durable=True` to also `fsync` the **parent directory**: the
rename is atomic but not _persisted_ across a power loss until the containing
directory is synced (the "fsync the directory too" nuance — see references).

## Fast walking

`walk_files` / `iter_dir` are built on `os.scandir`, whose `DirEntry` caches the
`stat` taken during the directory read, so `is_dir()` / `is_file()` cost no extra
syscall — markedly faster than `os.walk` / `Path.rglob`, which re-`stat` each
entry. `walk_files` does **not** descend symlinked directories or yield symlinked
files unless `follow_symlinks=True` (the safe default avoids cycles and escaping
`root`).

## Prior art

- **PEP 471** — `os.scandir` (Ben Hoyt, 2014): the `DirEntry` stat-cache that
  makes scandir walks beat `os.walk`/`rglob`.
- **The atomic-write idiom** (temp-file + `fsync` + atomic `rename`) as used by
  SQLite's rollback/WAL commit, Postgres, and CPython's `importlib` bytecode
  cache; the parent-directory-fsync durability nuance is documented in LWN's
  _"Ensuring data reaches disk"_ and the SQLite atomic-commit docs.
