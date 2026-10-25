"""proc — a safe, ergonomic runner over stdlib :mod:`subprocess`.

``subprocess`` is a sharp tool with three recurring foot-guns: ``shell=True``
(shell-injection when any argument is user-influenced), no timeout (a wedged
child hangs the caller forever), and ``check=True`` raising a bare
``CalledProcessError`` whose message *drops the child's stderr* — the one thing
you needed to debug the failure. This module is not a drop-in of every
``subprocess`` API; it is a **curated safe surface** that closes those three
holes and nothing more.

Backend: stdlib with optional bundled C acceleration, no third-party dependency.
On Linux and macOS, native spawning preserves descriptor isolation and a native
pipe loop accelerates captured output without stdin. Other shapes retain stdlib
execution. Bounded waits use kernel exit notifications where available. The
package works in a base ``unstd`` install with zero dependency closure.

The guarantees, versus a raw ``subprocess.run``:

- **``shell=False``, always.** :func:`run` accepts an argv *sequence* and never
  routes through a shell, so a metacharacter in an argument (``; rm -rf /``,
  ``$(…)``, backticks) is passed literally to the program — never interpreted.
  A bare ``str`` is *refused* (``TypeError``): split it yourself
  (``shlex.split``) so the tokenization is explicit and visible at the call site.
- **A default timeout.** Every call is bounded by :data:`DEFAULT_TIMEOUT`
  (override per-call). On expiry ``subprocess`` kills the child and
  :class:`TimeoutExpired` is raised — no call can
  hang forever.
- **Captured, decoded, structured result.** Output is always captured and
  text-decoded (utf-8, ``errors="replace"``) into a small frozen
  :class:`CompletedProcess` (``argv``/``returncode``/``stdout``/``stderr``/
  ``duration``).
- **``check=True`` that keeps the output.** A non-zero exit raises
  :class:`ProcessError` — a faithful ``subprocess.CalledProcessError`` subclass
  (so ``except proc.CalledProcessError`` still catches it) whose ``__str__``
  includes the captured stderr instead of losing it.

Convenience: :func:`run` (the full result), :func:`run_ok` (``bool`` — did it
succeed?), :func:`capture` (stdout, stripped).

Prior art: Python's stdlib :mod:`subprocess` (the surface hardened here) and its
own documented security note — *"the use of ``shell=True`` … is a security
hazard if combined with untrusted input"* — plus the well-worn advice to always
pass a timeout and to capture output before raising.
"""

from __future__ import annotations

import math
import os
import subprocess  # nosec B404 # the public API is a subprocess wrapper
import time
from typing import TYPE_CHECKING, TypedDict, Unpack

from unstd.proc._internal.backend import Popen as _Popen
from unstd.proc._internal.results import CompletedProcess, ProcessError


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


# Faithful re-exports so callers ``except proc.TimeoutExpired`` / ``proc.CalledProcessError``
# without importing stdlib ``subprocess`` alongside this safe surface. Deliberately
# not aliased to ``TimeoutError``: the builtin of that name is a different
# exception, and ``TimeoutExpired`` does not subclass it.
CalledProcessError = subprocess.CalledProcessError
TimeoutExpired = subprocess.TimeoutExpired

# Default wall-clock ceiling (seconds) for any call. Override per-call via
# ``timeout=``; ``None`` is refused so a call can never silently become unbounded.
DEFAULT_TIMEOUT: float = 60.0

__all__ = [
    "DEFAULT_TIMEOUT",
    "CalledProcessError",
    "CompletedProcess",
    "ProcessError",
    "TimeoutExpired",
    "capture",
    "run",
    "run_ok",
]


def _argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Coerce to a tuple of ``str``, refusing a bare string (the no-shell contract)."""
    if isinstance(argv, str | bytes):
        msg = (
            "proc.run refuses a bare string (it never routes through a shell); "
            "pass an argv list, e.g. shlex.split(cmd)"
        )
        raise TypeError(msg)
    return tuple(str(a) for a in argv)


class _RunKw(TypedDict, total=False):
    """The :func:`run` keyword surface, for the pass-through conveniences below."""

    timeout: float
    check: bool
    cwd: str | os.PathLike[str] | None
    env: Mapping[str, str] | None
    stdin_text: str | None
    encoding: str
    errors: str
    log: Callable[[Mapping[str, object]], None] | None


def run(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    check: bool = True,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    encoding: str = "utf-8",
    errors: str = "replace",
    log: Callable[[Mapping[str, object]], None] | None = None,
) -> CompletedProcess:
    """Run *argv* safely: no shell, bounded by *timeout*, output captured and decoded.

    Returns a :class:`CompletedProcess`. With ``check=True`` (default) a non-zero
    exit raises :class:`ProcessError` carrying the captured stderr. On expiry the
    child is killed and :class:`TimeoutExpired` is raised. *log*, if given, is
    invoked once with a structured record (``argv``/``returncode``/``duration``)
    — an injectable hook so callers can wire billog/logging without this module
    depending on either.

    ``timeout`` must be a positive, finite number of seconds. ``None``, bools,
    non-numeric values, zero, negative values, infinity and NaN are refused
    before launching a child.
    """
    cmd = _argv(argv)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        msg = "timeout must be a positive finite number of seconds"
        raise TypeError(msg)
    try:
        valid_timeout = math.isfinite(timeout) and timeout > 0
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        msg = "timeout must be a positive finite number of seconds"
        raise ValueError(msg)
    start = time.perf_counter()
    # Own the child explicitly. subprocess.run delegates exceptional reaping to
    # Popen.__exit__, whose KeyboardInterrupt path may skip wait() after
    # communicate() has already consumed its short SIGINT grace period.
    with _Popen(  # nosec B603 # shell=False + explicit argv
        cmd,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        encoding=encoding,
        errors=errors,
    ) as child:
        try:
            stdout, stderr = child.communicate(stdin_text, timeout=timeout)
        except TimeoutExpired as error:
            child.kill()
            if os.name == "nt":
                # Match subprocess.run's Windows reader-thread drain. POSIX
                # communicate has already attached partial bytes to the error;
                # draining there could block on a descendant retaining a pipe.
                # The stdlib also assigns text-mode results here; typeshed
                # describes TimeoutExpired output only as POSIX-style bytes.
                error.stdout, error.stderr = child.communicate()  # type: ignore[assignment]
            child.wait()
            raise
        except BaseException:
            child.kill()
            child.wait()
            raise
        returncode = child.wait()
    result = CompletedProcess(
        argv=cmd,
        returncode=returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        duration=time.perf_counter() - start,
    )
    if log is not None:
        log(
            {
                "event": "proc.run",
                "argv": cmd,
                "returncode": result.returncode,
                "duration": result.duration,
            }
        )
    if check and not result.ok:
        raise ProcessError(result)
    return result


def run_ok(argv: Sequence[str], **kw: Unpack[_RunKw]) -> bool:
    """Run *argv* and return whether it succeeded — ``True`` iff exit zero.

    Never raises for a non-zero exit or a timeout (both are just "not ok"), so it
    reads cleanly as a predicate: ``if proc.run_ok(["git", "diff", "--quiet"])``.
    ``check`` is forced off; other :func:`run` kwargs pass through.
    """
    kw["check"] = False
    try:
        return run(argv, **kw).ok
    except TimeoutExpired:
        return False


def capture(argv: Sequence[str], **kw: Unpack[_RunKw]) -> str:
    """Run *argv* and return its stdout, stripped — the ``$(cmd)`` idiom, made safe.

    ``check=True`` by default (a failing command raises :class:`ProcessError`
    rather than returning empty output); other :func:`run` kwargs pass through.
    """
    return run(argv, **kw).stdout.strip()
