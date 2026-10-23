"""Captured process outcomes and their exception representation."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess  # nosec B404 # exception compatibility, no process creation
from typing import override


@dataclass(frozen=True, slots=True)
class CompletedProcess:
    """The captured outcome of a finished child — immutable, decoded, self-describing."""

    __module__ = "unstd.proc"

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

    __module__ = "unstd.proc"

    def __init__(self, result: CompletedProcess) -> None:
        """Initialize the instance."""
        super().__init__(
            result.returncode,
            list(result.argv),
            output=result.stdout,
            stderr=result.stderr,
        )
        self.result = result

    @override
    def __str__(self) -> str:
        """The stdlib message, followed by the child's stderr."""
        base = super().__str__()
        tail = (self.stderr or "").strip()
        return f"{base}\n{tail}" if tail else base
