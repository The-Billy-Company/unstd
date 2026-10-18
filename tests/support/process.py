"""Bounded Python workers with explicit ownership and unconditional reaping.

Workers inherit the selected interpreter, but import the checkout explicitly so
temporary working directories cannot accidentally change the product under test.
Only fixture-owned children are launched; every caller supplies a wall deadline.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
import os
from queue import Queue
import signal
import subprocess
import sys
from threading import Thread
from typing import TYPE_CHECKING

from tests.support.anchors import REPO_ROOT


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path


def python_argv(code: str, args: Sequence[str] = ()) -> list[str]:
    """Build a worker command that imports this checkout from any directory."""
    prelude = f"import sys; sys.path[:0] = [{str(REPO_ROOT / 'src')!r}, {str(REPO_ROOT)!r}];\n"
    if os.environ.get("UNSTD_CHAOS_BACKEND") == "stdlib":
        prelude += "from tests.support.chaos import apply_backend_policy; apply_backend_policy();\n"
    return [sys.executable, "-u", "-c", prelude + code, *args]


@contextmanager
def python_worker(
    code: str,
    args: Sequence[str] = (),
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> Iterator[subprocess.Popen[str]]:
    """Own one piped worker and kill/reap it on every exit, including assertions."""
    owns_group = os.name == "posix" and os.environ.get("UNSTD_CHAOS_OWNED_GROUP") != "1"
    worker_env = dict(env) if env is not None else None
    if worker_env is not None:
        # A scenario's explicit environment cannot discard its outer supervisor
        # or import policy. Ordinary helper calls have neither private flag.
        for name in ("UNSTD_CHAOS_BACKEND", "UNSTD_CHAOS_OWNED_GROUP"):
            if name in os.environ:
                worker_env[name] = os.environ[name]
    child = subprocess.Popen(  # noqa: S603 — explicit interpreter, argv, no shell
        python_argv(code, args),
        cwd=cwd,
        env=worker_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=owns_group,
    )
    try:
        yield child
    finally:
        if owns_group:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
        elif child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def run_python(
    code: str,
    args: Sequence[str] = (),
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture a worker under a hard parent deadline, never leave it running."""
    with python_worker(code, args, cwd=cwd, env=env) as child:
        stdout, stderr = child.communicate(timeout=timeout)
        return subprocess.CompletedProcess(child.args, child.returncode, stdout, stderr)


def isolated_environment(**values: str) -> dict[str, str]:
    """Provide only interpreter/platform necessities plus fixture-owned values."""
    platform_keys = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
    return {**{k: os.environ[k] for k in platform_keys if k in os.environ}, **values}


def worker_line(child: subprocess.Popen[str], *, timeout: float) -> str:
    """Read a readiness line with a deadline; caller retains process ownership."""
    if child.stdout is None:
        msg = "worker has no stdout pipe"
        raise ValueError(msg)
    lines: Queue[str] = Queue(maxsize=1)
    reader = Thread(target=lambda: lines.put(child.stdout.readline()), daemon=True)
    reader.start()
    line = lines.get(timeout=timeout)
    reader.join(timeout=timeout)
    return line
