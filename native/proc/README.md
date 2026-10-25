# Private process kernels

Optional CPython acceleration for [`unstd.proc`](../../src/unstd/proc/README.md).
The Python backend owns the public API, timeout exceptions, text decoding and
child reaping. The private `_proc_native` module supplies isolated POSIX spawn
and captured-output exchange; unavailable facilities retain the Python path.

## Process creation

`spawn` admits the runner's ordinary POSIX shape and publishes the child's PID
and ownership on its `Popen` before returning through a signal checkpoint.
macOS uses `POSIX_SPAWN_CLOEXEC_DEFAULT` with explicit stdio actions. Linux uses
runtime-discovered `posix_spawn_file_actions_addclosefrom_np`; older libc builds
without that action decline before auditing or creating a child.

Child working directories use a pinned search-only directory descriptor and a
spawn `fchdir` action. Executable search uses the supplied environment's `PATH`,
including audit-hook changes. Missing search candidates are checked relative to
the child directory before spawning, avoiding an unsuccessful process creation
for each nonexistent PATH component. Failed executable candidates retain the
stdlib's first non-missing error precedence.

The `subprocess.Popen` audit event precedes native side effects. Arguments and
environment entries own references across conversions, including hooks that
mutate the audit payload or the source dictionary. A supplied environment stays
isolated; inherited environment bytes come from libc so direct `os.putenv`
changes are preserved. Owned C arrays survive releasing the GIL around spawn.
SIGPIPE/SIGXFZ/SIGXFSZ restoration follows stdlib behavior. Python retains final
pipe closure, exceptional kill-and-reap cleanup and waitpid status handling.

## Captured output

`communicate` drains stdout and stderr together with nonblocking reads and a
bounded `poll` loop. It returns owned output buffers, timeout state, observation state and EOF
state; the Python wrapper decodes completed output and preserves partial bytes
in timeout exceptions. The kernel restores descriptor flags on every exit and
never takes descriptor ownership. Calls with stdin retain stdlib's duplex
communication path.

Absolute deadlines use the same clock as Python: `mach_absolute_time` on macOS
and `CLOCK_MONOTONIC` on Linux. Signal checks occur after interrupted or completed
I/O, and native buffers are released when a handler raises. Readiness does not
mean the child has exited; bounded final waits remain with Python's pidfd/kqueue
backend.

## Build and qualification

The focused [build helper](experiments/build.py) compiles only this module:

```console
uv run --no-sync python native/proc/experiments/build.py
```

It builds in a temporary directory and atomically publishes a fresh extension
inode, preserving loaded Mach-O mappings. `--output /tmp/proc-native` selects a
separate installation root. The normal optional wheel build is configured in
[`hatch_build.py`](../../hatch_build.py).

Persistent contracts live in
[`test_native_spawn.py`](../../tests/suites/product/proc/unit/test_native_spawn.py)
and [`test_native_io.py`](../../tests/suites/product/proc/unit/test_native_io.py).
Native proof is separate from portable fallback coverage. Billy workload
receipts and paired measurements live under [`bench/`](../../bench/README.md).

The OS interfaces follow Apple's local `posix_spawn(2)`,
`posix_spawnattr_setflags(3)` and `posix_spawn_file_actions_addinherit_np(3)`
manuals, and glibc's documented spawn file actions. No shell evaluation or
parent descriptor-inheritance exception is introduced for speed.
