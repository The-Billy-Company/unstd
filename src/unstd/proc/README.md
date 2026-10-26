---
doc_radar:
  sentinels:
    - file: __init__.py
      contains: ['from unstd.proc.backend import']
    - file: results.py
      contains: ['__module__ = "unstd.proc"']
---

# `unstd.proc`

Part of [`unstd`](../README.md). A safe, ergonomic runner over the
stdlib [`subprocess`](https://docs.python.org/3/library/subprocess.html) module —
it hardens the three recurring `subprocess` foot-guns and offers nothing else. It
is a **curated safe surface**, not a drop-in of every `subprocess` API.

## Backend

**No extra or third-party dependency.** The base install includes an optional
bundled C backend on CPython and retains a stdlib fallback.

macOS uses native spawning that closes unrelated descriptors and preserves
argv, cwd, PATH, environment and subprocess audit events. Linux retains stdlib
process creation after paired profiles showed lower startup costs for its
ordinary commands. Both use a native poll/read loop with private growing buffers
for captured output without stdin. Text decoding,
universal newlines, timeout exceptions and final reaping retain their existing
contracts. Calls supplying `stdin_text` use stdlib pipe communication.

Bounded child waits use Linux pidfds or macOS kqueue exit notifications when
available. This removes stdlib's exponential polling sleeps after captured
pipes close, including when a child closes stdout/stderr before it exits.
Already completed children are checked before allocating a notification handle.
Unavailable or restricted kernel facilities fall back to the stdlib wait with
the remaining deadline; Windows retains stdlib execution. Unsupported
native spawn shapes use `subprocess` without relaxing descriptor isolation.

See the [process reports](../../../bench/reports/proc/README.md) for workload
and memory-safety qualification and earlier exit-wait measurements.

## What it hardens

| `subprocess` foot-gun          | What `proc` does                                                                                                        |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `shell=True` shell-injection   | `shell=False` **always**; a bare `str` argv is refused (`TypeError`) — split it yourself so tokenization is explicit    |
| No timeout → hangs forever     | every call is bounded by `DEFAULT_TIMEOUT` (60s, override per-call); expiry kills the child and raises `TimeoutExpired` |
| `check=True` drops stderr      | output is always captured; a non-zero exit raises `ProcessError` whose `str()` **includes** the captured stderr         |
| Raw `bytes` / undecoded output | text-decoded by default (`utf-8`, `errors="replace"`) into a small frozen result                                        |

## Surface

```python
from unstd import proc

r = proc.run(["git", "rev-parse", "HEAD"])  # -> CompletedProcess (raises on non-zero)
r.returncode, r.stdout, r.stderr, r.duration, r.argv, r.ok

ok = proc.run_ok(["git", "diff", "--quiet"])  # -> bool; never raises on failure/timeout
sha = proc.capture(["git", "rev-parse", "HEAD"])  # -> str, stripped

proc.run(
    ["make", "build"], timeout=300, log=logger.info
)  # per-call timeout + structured hook
```

`run(argv, *, timeout=DEFAULT_TIMEOUT, check=True, cwd=None, env=None,
stdin_text=None, encoding="utf-8", errors="replace", log=None)`. The `log` hook
is an injectable `Callable[[Mapping], None]` invoked once with
`{"event", "argv", "returncode", "duration"}` — so callers wire billog/`logging`
without `proc` depending on either.

### Errors — faithful re-exports

`proc.ProcessError` subclasses stdlib `subprocess.CalledProcessError`, so
`except proc.CalledProcessError` (or `except subprocess.CalledProcessError`)
catches it. `proc.TimeoutExpired` is the stdlib class re-exported. It is deliberately not
aliased as `TimeoutError`: a timed-out child raises `TimeoutExpired`, which does
_not_ subclass the builtin `TimeoutError`, so the alias read as a promise the
exception couldn't keep.

## Prior art

- Python stdlib [`subprocess`](https://docs.python.org/3/library/subprocess.html)
  — the surface hardened here, and its own [security note](https://docs.python.org/3/library/subprocess.html#security-considerations)
  on `shell=True` with untrusted input.

## Source ownership

The package is one process execution code leaf. [`__init__.py`](__init__.py)
owns public orchestration, validation, timeout cleanup and logging.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public runner, validation and cleanup policy |
| [`backend.py`](backend.py) | Optional native spawning, capture and bounded exit waits |
| [`results.py`](results.py) | Captured outcomes and process errors |
| [`_proc_native.pyi`](_proc_native.pyi) | Type contract for the optional native kernel |

Result classes retain `unstd.proc` as their public identity for imports and
pickles. `backend.py` subclasses stdlib `Popen` at its spawning, communication
and bounded-wait seams. macOS admits ordinary native POSIX spawning; Linux
keeps stdlib creation. Both can capture stdout/stderr without stdin through
the native kernel. Unsupported shapes retain stdlib execution, text decoding,
SIGINT grace, waitpid synchronization and final reaping.

Linux pidfds and macOS kqueue notifications avoid polling between pipe EOF and
child exit. Notification handles close on completion, timeout and cancellation;
unavailable facilities fall back with the remaining deadline. No subprocess
globals, signal handlers or worker threads are changed.

The [native source guide](../../../native/proc/README.md) explains argument and
environment ownership, output buffers, audit handling and sanitizer checks.
The [captured-consumer qualification](../../../bench/reports/proc/billy-native.md)
records native spawn/capture costs and fallback cases on macOS and Linux,
including unchanged workloads and measured slowdowns.
Run the contracts from the repository root with
`uv run --no-sync python -m pytest tests/suites/product/proc -q`.
