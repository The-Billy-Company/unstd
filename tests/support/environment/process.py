"""Bounded Python workers with explicit ownership and unconditional reaping.

Workers inherit the selected interpreter and the parent's installed-or-source
product choice, so temporary directories cannot change the product under test.
Only fixture-owned children are launched; every caller supplies a wall deadline.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
from importlib.util import find_spec
import os
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
from threading import Thread
from typing import TYPE_CHECKING

from tests.support.environment.anchors import REPO_ROOT


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence


_GROUP_EVIDENCE_LIMIT = 65536


def _darwin_group_finished(pgid: int) -> bool:
    """Accept only bounded OS evidence of absence or owned zombie-only membership."""
    try:
        observed = subprocess.run(  # noqa: S603 — OS-owned ps, literal numeric group
            ["/bin/ps", "-o", "pid=,pgid=,uid=,stat=", "-g", str(pgid)],
            capture_output=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if observed.stderr or len(observed.stdout) > _GROUP_EVIDENCE_LIMIT:
        return False
    # Darwin ps exits 1 without output when the selected group has disappeared.
    if observed.returncode == 1 and not observed.stdout:
        return True
    if observed.returncode != 0 or not observed.stdout:
        return False
    try:
        rows = observed.stdout.decode("ascii").splitlines()
        for row in rows:
            pid, group, owner, state = row.split()
            if (
                int(pid) <= 1
                or int(group) != pgid
                or int(owner) != os.getuid()
                or not state.startswith("Z")
                or any(flag not in "+<>AELNSsVWX" for flag in state[1:])
            ):
                return False
    except (UnicodeError, ValueError):
        return False
    return bool(rows)


def kill_owned_process_group(pgid: int) -> None:
    """Kill an owned group, accepting Darwin's verified terminal-zombie race.

    Darwin can return EPERM for a group whose only remaining members are
    zombies. Inspect that rare error once; live, foreign or uncertain evidence
    retains the permission failure. Never retry a signal after observing exit.
    The caller still owns direct-child reaping.
    """
    if pgid <= 1:
        msg = "owned process group must have an id greater than 1"
        raise ValueError(msg)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as error:
        if (
            sys.platform != "darwin"
            or error.errno != errno.EPERM
            or not _darwin_group_finished(pgid)
        ):
            raise


def python_argv(code: str, args: Sequence[str] = ()) -> list[str]:
    """Preserve the parent's product origin and repository support imports."""
    paths = [str(REPO_ROOT)]
    package = find_spec("unstd")
    if package is not None and package.origin is not None:
        # Extracted wheels can precede an older installed distribution through
        # PYTHONPATH, which an explicit child environment deliberately drops.
        paths.insert(0, str(Path(package.origin).resolve().parent.parent))
    prelude = f"import sys; sys.path[:0] = {paths!r};\n"
    if os.environ.get("UNSTD_CHAOS_BACKEND") == "stdlib":
        prelude += "from tests.support.chaos.journal import apply_backend_policy; apply_backend_policy();\n"
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
        # Scenario environments retain explicitly armed campaign policy and
        # sanitizer runtimes required by instrumented extension imports.
        inherited = ["UNSTD_CHAOS_BACKEND", "UNSTD_CHAOS_OWNED_GROUP"]
        if os.environ.get("UNSTD_NATIVE_SANITIZERS") == "1":
            inherited.extend(
                (
                    "UNSTD_NATIVE_SANITIZERS",
                    "LD_PRELOAD",
                    "ASAN_OPTIONS",
                    "UBSAN_OPTIONS",
                    "PYTHONMALLOC",
                )
            )
        for name in inherited:
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
        try:
            if owns_group:
                kill_owned_process_group(child.pid)
            elif child.poll() is None:
                child.kill()
        except BaseException as error:
            # A group-cleanup failure must not skip ownership of the direct child.
            try:
                child.kill()
                child.communicate(timeout=5)
            except BaseException as cleanup_error:
                error.add_note(f"direct-child cleanup failed: {cleanup_error}")
            raise
        else:
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
