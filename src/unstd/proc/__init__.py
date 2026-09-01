"""proc — a safe, ergonomic runner over stdlib :mod:`subprocess`.

``subprocess`` is a sharp tool with three recurring foot-guns: ``shell=True``
(shell-injection when any argument is user-influenced), no timeout (a wedged
child hangs the caller forever), and ``check=True`` raising a bare
``CalledProcessError`` whose message *drops the child's stderr* — the one thing
you needed to debug the failure. This module is not a drop-in of every
``subprocess`` API; it is a **curated safe surface** that closes those three
holes and nothing more.

Backend: PURE STDLIB — no optional extra, no third-party backend.
Its value is *safety* guardrails, not speed, so there is nothing to accelerate;
depending on it costs zero closure and it works in a base ``unstd`` install.

The guarantees, versus a raw ``subprocess.run``:

- **``shell=False``, always.** :func:`run` accepts an argv *sequence* and never
  routes through a shell, so a metacharacter in an argument (``; rm -rf /``,
  ``$(…)``, backticks) is passed literally to the program — never interpreted.
  A bare ``str`` is *refused* (``TypeError``): split it yourself
  (``shlex.split``) so the tokenization is explicit and visible at the call site.
- **A default timeout.** Every call is bounded by :data:`DEFAULT_TIMEOUT`
  (override per-call). On expiry ``subprocess`` kills the child and
  :class:`TimeoutExpired` (aliased :data:`TimeoutError`) is raised — no call can
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

from dataclasses import dataclass
import subprocess
import time
from typing import TYPE_CHECKING, TypedDict, Unpack


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    import os


# Faithful re-exports so callers ``except proc.TimeoutError`` / ``proc.CalledProcessError``
# without importing stdlib ``subprocess`` alongside this safe surface. ``TimeoutError``
# is an ergonomic alias for ``subprocess.TimeoutExpired`` (which, unlike the builtin
# ``TimeoutError``, is what a timed-out child actually raises).
CalledProcessError = subprocess.CalledProcessError
TimeoutExpired = subprocess.TimeoutExpired
TimeoutError = subprocess.TimeoutExpired

# Default wall-clock ceiling (seconds) for any call. Override per-call via
# ``timeout=``; ``None`` is refused so a call can never silently become unbounded.
DEFAULT_TIMEOUT: float = 60.0

__all__ = [
    "DEFAULT_TIMEOUT",
    "CalledProcessError",
    "CompletedProcess",
    "ProcessError",
    "TimeoutError",
    "TimeoutExpired",
    "capture",
    "run",
    "run_ok",
]


@dataclass(frozen=True, slots=True)
class CompletedProcess:
    """The captured outcome of a finished child — immutable, decoded, self-describing."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration: float

    @property
    def ok(self) -> bool:
        """``True`` iff the child exited zero."""
        return self.returncode == 0


class ProcessError(subprocess.CalledProcessError):
    """Non-zero exit — a ``CalledProcessError`` that keeps (and prints) the output.

    Subclasses stdlib ``CalledProcessError`` so existing ``except
    subprocess.CalledProcessError`` / ``proc.CalledProcessError`` clauses keep
    catching it, but carries the full :class:`CompletedProcess` and folds the
    captured stderr into ``str(exc)`` — the detail a bare ``CalledProcessError``
    throws away.
    """

    def __init__(self, result: CompletedProcess) -> None:
        """Initialize the instance."""
        super().__init__(
            result.returncode,
            list(result.argv),
            output=result.stdout,
            stderr=result.stderr,
        )
        self.result = result

    def __str__(self) -> str:
        """Str."""
        base = super().__str__()
        tail = (self.stderr or "").strip()
        return f"{base}\n{tail}" if tail else base


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
    """
    cmd = _argv(argv)
    start = time.perf_counter()
    completed = subprocess.run(  # noqa: S603 — shell=False + explicit argv is the safe path
        cmd,
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=stdin_text,
        text=True,
        encoding=encoding,
        errors=errors,
        check=False,
    )
    result = CompletedProcess(
        argv=cmd,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
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
