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

These are syscall-error and process-interruption contracts. They do not simulate
power loss or prove storage-device persistence. Directory sync remains a
best-effort portability operation. POSIX-only symlink and descriptor controls
carry precise platform guards; ordinary stream and process cases remain portable.
