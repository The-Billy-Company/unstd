# `unstd.proc`

Part of [`unstd`](../README.md). A safe, ergonomic runner over the
stdlib [`subprocess`](https://docs.python.org/3/library/subprocess.html) module —
it hardens the three recurring `subprocess` foot-guns and offers nothing else. It
is a **curated safe surface**, not a drop-in of every `subprocess` API.

## Backend

**Pure stdlib — no extra, no third-party backend.** Unlike `serde`/`ids`/`time`,
`proc`'s value is _safety_, not speed, so there is nothing to accelerate. It
imports and works in a base `unstd` install and adds zero dependency closure.

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
catches it. `proc.TimeoutExpired` is the stdlib class re-exported; `proc.TimeoutError`
is an ergonomic alias for it (a timed-out child raises `TimeoutExpired`, _not_
the builtin `TimeoutError`).

## Prior art

- Python stdlib [`subprocess`](https://docs.python.org/3/library/subprocess.html)
  — the surface hardened here, and its own [security note](https://docs.python.org/3/library/subprocess.html#security-considerations)
  on `shell=True` with untrusted input.
