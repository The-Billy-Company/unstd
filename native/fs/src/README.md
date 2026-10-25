# Linux atomic writes

`adapter.c` implements the private `unstd.fs._fs_native.atomic_write` entrypoint.
The public wrapper retains other text encodings, custom behavior, and the Python
fallback. Admission requires exact bytes or UTF-8 text, supported durability, a
normalized filename, and an existing directory parent. Missing or non-directory
parents and unsupported names decline before mutations, leaving recursive
creation and its precise failures to Python.

The private call returns `None` on success and `NotImplemented` when it declines
before scratch creation. Exceptions propagate through the public wrapper without
a second write. The wrapper converts the input path once and returns its `Path`
after success. Streaming `atomic_writer` remains Python.

Unavailable or policy-blocked `getrandom` (`ENOSYS`/`EPERM`) also declines before
scratch creation, retaining Python's secure `/dev/urandom` fallback. A strict
directory preflight may already have succeeded; repeating that acknowledgment
in Python does not publish or mutate a file. Python uses these same capability
errors in [`bootstrap_hash.c`](https://raw.githubusercontent.com/python/cpython/v3.14.8/Python/bootstrap_hash.c).

UTF-8 text is encoded after scratch acquisition and mode copying, matching the
Python stream-write stage. Encoding and allocation failures therefore retain
the original exception while closing and discarding the acquired scratch.

Operation syscalls release the GIL and check Python signals on return. Cleanup
also releases the GIL while preserving the primary exception already raised.
Acquired file descriptors are recorded before that check; a cancellation at any
pre-commit boundary closes and discards the scratch while preserving the
original exception. EINTR retries occur only after a signal check. Linux close
is never retried because its descriptor may already have been reused.

Successful rename is marked committed before checking signals. A cancellation
after that boundary retains the replacement and carries a committed-state note.
Strict directory syncing preserves preflight, post-commit acknowledgment, and
directory-close cleanup notes. No runtime fault-injection flags are compiled
into the kernel; qualification uses a separate ELF observer.

Scratch creation retains the Python path's private initial mode, existing-mode
copying through the owned descriptor, exclusive random name, and 14-byte
basename overhead. Each write performs file fsync and a same-directory rename;
durability modes retain their directory-sync obligations. Cleanup failures add
notes to the primary exception instead of replacing it.

See the [filesystem native overview](../README.md) for builds, source ownership,
and the [installed-package qualification](../../../bench/reports/fs/production/README.md)
for interpreter, artifact, fallback and consumer-workload evidence.
