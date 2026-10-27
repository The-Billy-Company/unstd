---
doc_radar:
  sentinels:
    - file: __init__.py
      contains: ['from unstd.fs.durability import']
    - file: __init__.py
      contains: ['def ensure_dir(path: _Pathish, *, check_first: bool = True)', 'if not check_first or not p.is_dir():']
    - file: traversal.py
      contains: ['def _walk_descriptors(']
---

# `unstd.fs` — filesystem operations `pathlib`/`os` lack

Part of [`unstd`](../README.md). **No optional extra or third-party runtime dependency.**
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
| `atomic_write(path, data, *, encoding="utf-8", durable=False)`            | Atomic write of `str`/`bytes`: temp file in the same dir → `fsync` → `os.replace`.                                   |
| `atomic_writer(path, *, mode="w"\|"wb", encoding="utf-8", durable=False)` | Context-manager form — yields a handle; commits atomically on clean exit, discards the temp file if the body raises. |
| `atomic_replace(src, dest, *, durable=False)`                             | Atomic `os.replace` of an existing file onto `dest` (same-filesystem rename).                                        |
| `ensure_dir(path, *, check_first=True)`                                    | Create missing directories; optionally retain direct `Path.mkdir` attempts and audit events.                         |
| `sync_dir(path, *, strict=False)`                                         | Directory `fsync`; OS errors are suppressed unless strict mode is requested.                                       |
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

## Linux acceleration and fallback

Ordinary GIL-enabled Linux CPython wheels include a private native backend for
`atomic_write`, without an extra dependency. It handles exact `bytes`, or exact
`str` with the default `encoding="utf-8"`, and an existing parent directory.
It owns scratch creation, writing, file syncing, closing, replacement, cleanup,
and the requested directory-sync policy. Non-Linux platforms, other interpreters
and free-threaded builds retain the stdlib-based Python implementation.

Other encodings, subclasses, missing or non-directory parents, unsupported
pathnames, and replaced OS/path/orchestration bindings retain Python behavior.
An absent extension or optional local compiler failure also retains Python.
Unavailable or policy-blocked native entropy (`getrandom` returning `ENOSYS`
or `EPERM`) declines before scratch creation and uses Python's secure fallback.
Native I/O errors propagate; they never trigger a second write through Python.
`atomic_writer` continues to provide the Python streaming context manager.

Source builds attempt the extension automatically. Local compiler failure keeps
the package usable; `CIBUILDWHEEL=1` makes compilation failure fatal for release
builds. [Native build and source ownership](../../../native/fs/README.md)
documents the private kernel and qualification tools.

The native writer checks Python signals at syscall boundaries while retaining
ownership of acquired descriptors. Cancellation before replacement attempts
scratch cleanup and preserves the destination. Cleanup errors add notes to the
primary exception. A cancellation or strict-sync error
after successful replacement reports that the new data is already committed.
UTF-8 encoding happens after scratch acquisition, preserving stream-stage
error ordering. The Python writer also confirms committed inode identity when
a signal interrupts the return from a successful rename.

The [installed-package qualification](../../../bench/reports/fs/production/README.md)
records cancellation/error tests, compiler-failure fallback, Linux interpreter
coverage, and 13 consumer workload shapes exercised through the installed public
API. Its small-write CPU gains do not establish a uniform disk-latency gain.

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

Use `ensure_dir(path, check_first=False)` when porting a direct `Path.mkdir`
call. It keeps that call's creation audit event and skips the preliminary
directory query. The default still checks first and leaves existing directories
as audit-free no-ops.

The evaluation includes real file and directory syncing. Local I/O timing is
noisy, so it does not establish a consistent atomic-write speedup. The portable
walk retains its per-entry identity checks.

The earlier [Linux consumer evaluation](../../../bench/reports/fs/README.md)
profiles actual consumer shapes and a separately built proof. The production
follow-up above qualifies installed public dispatch; the historical proof
remains outside the package build.

## Source ownership

The package is one filesystem code leaf. [`__init__.py`](__init__.py) owns
public calls, atomic-write orchestration and platform dispatch, passing its
operating-system binding explicitly to the traversal and synchronization code.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public operations, atomic commits and native dispatch |
| [`durability.py`](durability.py) | Directory synchronization and descriptor cleanup |
| [`traversal.py`](traversal.py) | Descriptor-anchored, portable and symlink-following walks |
| [`_fs_native.pyi`](_fs_native.pyi) | Type contract for the optional Linux kernel |

The descriptor walker caches directory-name prefixes and constructs `Path`
objects only for yielded files. Each traversal frame owns its descriptor and
scanner; cleanup unwinds every live ancestor even if another close fails.
Live handles remain bounded by traversal depth. Atomic-write orchestration
keeps its injectable sync and cleanup hooks in the public package.

Linux `atomic_write` can dispatch to the kernel in
[`native/fs/src/`](../../../native/fs/src/README.md). Unsupported inputs,
unavailable kernels and custom OS/path bindings retain Python execution.
Streaming `atomic_writer` stays in Python and checks inode identity when
reporting cancellation after a rename.

Run the contracts from the repository root with
`uv run --no-sync python -m pytest tests/suites/product/fs -q`.
