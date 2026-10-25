# Filesystem integration evidence

Run `uv run pytest tests/suites/product/fs/integration -q` from the repository root.
This leaf belongs to the `integration` inventory lane and runs in the normal
suite, including the base dependency configuration.

`test_atomic_failures.py` injects failures at real stream and OS boundaries:
scratch creation, descriptor wrapping, partial writes, flush, file fsync, close,
permission copying, replacement, and rollback unlink. Every fault records that
its seam was reached. Assertions retain the exact prior bytes or prior absence,
check descriptor release and scratch ownership, and perform a successful recovery
write. Concurrent cleanup errors retain the original exception and add a note
identifying failed cleanup and any orphan; they cannot turn the original failure
into a misleading secondary error. Real encoding failures exercise text streams.

`test_filesystem_boundaries.py` covers repeated scratch-name collisions with
files, directories and symlinks, symlink replacement without changing its
referent, non-inherited scratch descriptors, parent-directory handle cleanup,
file-sync/rename/directory-sync ordering, best-effort directory sync failures,
strict sync errors and preflight, newly created parent acknowledgments,
post-commit failure state and cross-directory strict moves,
atomic-replace refusal, blocked parent paths, and traversal escape/cycle controls.
OS injection is scoped to the product module rather than patching Python's
global `os` functions. Tests never change the process-wide umask.

`test_atomic_interruption.py` kills fixture-owned writers only after an explicit
pipe readiness message at partial-write, file-synced, and committed barriers.
A pre-commit kill may leave one identifiable scratch file; prior destination
bytes must remain exact. A post-commit kill must retain the whole replacement.
Three simultaneous readers check exact old bytes at each flushed half-scratch
barrier, allow only the current old/new versions while commit races, then check
exact new bytes after every commit across 64 generations. Per-reader counters
must prove all 64 pre-commit and post-commit reads occurred. Worker and readiness
deadlines bound hangs, and fixture teardown kills and reaps owned workers.

[`test_native_atomic.py`](test_native_atomic.py) adds 62 Linux contracts against
the real compiled native entrypoint and the public `fs.atomic_write` API. Profile
events prove that an admitted call enters the compiled builtin without Python
fallback; comparison children explicitly set `fs._native=None`. Ordinary cases
cover byte/UTF-8 publication, private creation under restrictive umasks, retained
sharing modes, encoding-error fields and cleanup, other-codec fallback, and
filesystem rejection errors.

Its test-only [ELF observer](../../../../../native/fs/qualification/README.md)
performs real successful I/O and delivers signals synchronously at selected
syscall returns. Pre-commit cancellation must retain prior bytes and release
owned descriptors/scratch files. Post-rename cancellation must retain complete
new bytes and identify the committed replacement. Custom cancellation and
KeyboardInterrupt exercise the pre-commit write, file-sync and close seams;
native scratch-open cancellation additionally checks acquired ownership.
Strict directory-sync checks distinguish preflight from post-commit failure.
Other controls cover short writes, EINTR, Linux close without retry, precise
error/filename fields, cleanup failures that retain the primary exception, and
ENOSYS/EPERM entropy refusal before scratch creation with successful Python
fallback. Each child has a fixed timeout and checks its descriptor delta.

Native cases carry Linux/backend guards. A missing C compiler skips only cases
that need the observer, which is built in a private temporary directory. Child
launches preserve an existing sanitizer preload before appending the observer.
No observer is loaded during [installed Billy profiling](../../../../../bench/reports/fs/production/README.md).

These are syscall-error and process-interruption contracts. They do not simulate
power loss or prove storage-device persistence. Default directory sync remains
best effort; strict mode requires OS acknowledgments and propagates failures.
POSIX-only symlink and descriptor controls
carry precise platform guards; ordinary stream and process cases remain portable.
