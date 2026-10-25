# `unstd.fs` — filesystem operations `pathlib`/`os` lack

Part of [`unstd`](../README.md). **Pure stdlib — no optional extra.**
`pathlib` is best-in-class path _algebra_ (joins, suffixes, parents); it stops at
the durable-write and fast-walk _operations_ every service otherwise re-rolls.
`unstd.fs` is exactly those operations — it is **not** a `pathlib` replacement.
Keep `Path` for path math; reach here for the I/O around it. Path-returning
operations use `pathlib.Path`; `atomic_writer` yields a writable handle and
`sync_dir` returns `None`.

```python
from unstd import fs
```

## Surface

| Function                                                                  | Does                                                                                                                 |
| ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `atomic_write(path, data, *, encoding="utf-8", durable=False)`            | Crash-safe write of `str`/`bytes`: temp file in the same dir → `fsync` → `os.replace`.                               |
| `atomic_writer(path, *, mode="w"\|"wb", encoding="utf-8", durable=False)` | Context-manager form — yields a handle; commits atomically on clean exit, discards the temp file if the body raises. |
| `atomic_replace(src, dest, *, durable=False)`                             | Atomic `os.replace` of an existing file onto `dest` (same-filesystem rename).                                        |
| `ensure_dir(path)`                                                        | `mkdir(parents=True, exist_ok=True)`, idempotent.                                                                    |
| `sync_dir(path)`                                                          | Best-effort directory `fsync`; OS errors are suppressed.                                                           |
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

If the body raises, the destination is left as it was and cleanup attempts to
remove the scratch file. Cleanup failures are attached as notes to the original
exception and can leave a scratch file behind. A killed writer may also leave
one. Pass `durable=True` to attempt `fsync` on the parent directory after the
rename. `sync_dir` suppresses OS errors, so this is best effort and does not
guarantee persistence when directory syncing fails or is unsupported.

Use `durable="strict"` when directory-sync failures must be visible. It requires
successful OS acknowledgments for newly created parent entries and the final
replacement. It preflights directory syncing before replacing the destination,
so unsupported platforms fail before commit. A later I/O failure can still occur
after replacement; the exception includes a note that the new data is already
committed. Retrying must account for that state, rather than assuming rollback.

```python
fs.atomic_write(path, payload, durable="strict")
fs.sync_dir(path.parent, strict=True)
```

`sync_dir(..., strict=True)` reports open, fsync and close errors, and refuses an
ordinary file handle. The default continues to suppress OS errors. Strict mode
requires platform directory-fsync support; it does not simulate power loss or
override filesystem/storage-device persistence semantics. Paths and their parent
directories must be trusted against concurrent namespace replacement.

`atomic_replace(..., durable="strict")` preflights and syncs both source and
destination directories. As with the legacy mode, it does not flush source-file
contents; the caller must sync those before moving when content durability is
required. A post-rename sync failure leaves the rename committed.

`atomic_writer` accepts only `"w"` and `"wb"`. Unsupported modes raise
`ValueError` before creating directories or a scratch file.

Rewriting an existing file preserves its permissions from scratch creation, so
an initially broader mode cannot leak replacement data through a retained read
handle. Where descriptor chmod is available, mode copying targets the acquired
file rather than following a replaced scratch path. New files request owner-only
`0o600`, with the process umask applied by the kernel. They never grant group or
world access by default, including under a permissive `0o000` umask. This is
deliberately stricter than ordinary `open()` creation; `0o022` still produces
`0o600`. Rewrites preserve the destination's existing mode exactly, including an
explicitly chosen group-sharing or world-write policy. Set a new file's sharing
policy explicitly when needed. The writer never reads or changes the
process-wide umask.

Reads and one-level listings are `Path.read_bytes` / `read_text` / `iterdir`:
CPython already sizes a whole-file read from `fstat` and lists through `scandir`,
so a wrapper here measured slower, not faster.

## Walking and concurrent directory changes

`walk_files` uses `os.scandir` and reuses each `DirEntry`'s cached type
information. The default skips symlinked directories and files, including a
symlink supplied as `root`.

On platforms supporting descriptor-relative opens, `O_DIRECTORY`, `O_NOFOLLOW`,
and `scandir(fd)` (including Linux and macOS), it opens each directory relative
to its already-open parent. Replacing a directory with a symlink before descent,
or renaming and replacing an opened ancestor, cannot redirect enumeration into
the symlink target. Open directories stay anchored to the tree being traversed.
The initial root's parent components are resolved normally when the root is
opened; the caller must trust that initial root selection.

Live directory handles and scanners grow with traversal depth, rather than the
number of siblings. Iteration is lazy; exhaustion, errors, and `close()` release
all acquired handles. Close an unfinished iterator explicitly when stopping early.
Unreadable or vanished directories are skipped. Entry order is unspecified.

Other platforms, notably Windows, keep the portable path walk. It rejects
directory reparse points and rechecks directory identity before and after opening
a scan, including directories that changed while queued. These checks are best
effort: this fallback is **not a confinement boundary** against concurrent
namespace changes.

Every yielded `Path` is a name, not an open file handle. A later open can follow a
replacement even after a descriptor-protected walk. Applications that need to
read untrusted, concurrently modified trees must also protect their file opens.
`follow_symlinks=True` retains ordinary path traversal, including links outside
`root` and possible symlink cycles.

## Prior art

- **PEP 471** — `os.scandir` (Ben Hoyt, 2014): the `DirEntry` stat-cache that
  makes scandir walks beat `os.walk`/`rglob`.
- **The atomic-write idiom** (temp-file + `fsync` + atomic `rename`) as used by
  SQLite's rollback/WAL commit, Postgres, and CPython's `importlib` bytecode
  cache; the parent-directory-fsync durability nuance is documented in LWN's
  _"Ensuring data reaches disk"_ and the SQLite atomic-commit docs.

## Performance evidence

The [backend evaluation](../../../bench/reports/fs/README.md) retains paired
wall/CPU measurements and profiles on Python 3.12, 3.13, and 3.14. Descriptor
traversal caches directory-name prefixes and constructs `Path` objects only
for returned files. Existing-directory checks avoid a failed `mkdir`; creating
a missing directory incurs an initial directory check. Strict writers reuse
their successful parent check before preflight syncing.

The evaluation includes real file and directory syncing. Local I/O timing is
noisy, so it does not establish a consistent atomic-write speedup. The portable
walk retains its per-entry identity checks.

The [Billy/Linux follow-up](../../../bench/reports/fs/billy-linux.md) profiles
actual consumer shapes and a separately built native atomic-write proof. That
experiment is outside public fs dispatch; its cancellation and error contracts
require further qualification before integration.

## Implementation layout

Traversal and directory-sync mechanisms live in [`_internal/`](_internal/README.md). The package keeps atomic-write orchestration and passes its OS binding explicitly to the private helpers.
