"""Stateful fault schedules over real files and fixture-owned child processes.

These are experiments, not replacements for the product APIs. A schedule retains
one independent committed-state model across failures and recoveries. Faults are
module-local facades: the test interpreter and unrelated libraries retain their
real OS. Callers must put each schedule inside a hard-bounded worker.
"""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
from typing import IO, TYPE_CHECKING, Any

from unstd import fs, proc


if TYPE_CHECKING:
    from collections.abc import Callable


ATOMIC_ACTIONS = (
    "success",
    "open",
    "fdopen",
    "chmod",
    "write-enospc",
    "file-sync",
    "replace",
    "write-close",
    "write-unlink",
    "directory-open",
    "directory-sync",
    "directory-close",
)
PROCESS_ACTIONS = ("success", "nonzero", "early-stdin-close", "timeout")


def _digest(value: bytes | None) -> str | None:
    return None if value is None else hashlib.sha256(value).hexdigest()


class _Stream:
    def __init__(self, stream: IO[Any], boundary: _AtomicBoundary) -> None:
        self.stream = stream
        self.boundary = boundary

    def write(self, data: bytes) -> int:
        if self.boundary.action in {"write-enospc", "write-close", "write-unlink"}:
            self.stream.write(data[: len(data) // 2])
            self.boundary.hit("write", self.boundary.primary)
        return self.stream.write(data)

    def flush(self) -> None:
        self.stream.flush()

    def fileno(self) -> int:
        return self.stream.fileno()

    def close(self) -> None:
        self.stream.close()
        if self.boundary.action == "write-close":
            self.boundary.hit("stream-close", OSError(errno.EIO, "secondary close"))


class _AtomicBoundary:
    def __init__(self, action: str, parent: Path) -> None:
        self.action = action
        self.parent = parent
        self.hits: list[str] = []
        self.descriptors: list[int] = []
        number = errno.ENOSPC if action.startswith("write") else errno.EIO
        self.primary = OSError(number, f"scheduled {action}")

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def hit(self, name: str, error: OSError) -> None:
        self.hits.append(name)
        raise error

    def open(self, path: Path, flags: int, mode: int = 0o600) -> int:
        directory = Path(path) == self.parent
        if (directory and self.action == "directory-open") or (
            not directory and self.action == "open"
        ):
            self.hit("directory-open" if directory else "open", self.primary)
        fd = os.open(path, flags, mode)
        self.descriptors.append(fd)
        return fd

    def fdopen(self, fd: int, *args: Any, **kwargs: Any) -> _Stream:
        if self.action == "fdopen":
            self.hit("fdopen", self.primary)
        return _Stream(os.fdopen(fd, *args, **kwargs), self)

    def chmod(self, path: Path, mode: int) -> None:
        if self.action == "chmod":
            self.hit("chmod", self.primary)
        os.chmod(path, mode)  # noqa: PTH101 — exercise the real chmod boundary

    if hasattr(os, "fchmod"):

        def fchmod(self, fd: int, mode: int) -> None:
            if self.action == "chmod":
                self.hit("chmod", self.primary)
            os.fchmod(fd, mode)

    def fsync(self, fd: int) -> None:
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (directory and self.action == "directory-sync") or (
            not directory and self.action == "file-sync"
        ):
            self.hit("directory-sync" if directory else "file-sync", self.primary)
        os.fsync(fd)

    def close(self, fd: int) -> None:
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        os.close(fd)
        if directory and self.action == "directory-close":
            self.hit("directory-close", self.primary)


def atomic_schedule(
    directory: Path,
    actions: tuple[str, ...],
    *,
    record: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run a mixed write/rollback/recovery history against one committed model."""
    assert actions, "empty fault schedule"
    assert set(actions) <= set(ATOMIC_ACTIONS), "unknown fault schedule"
    dest = directory / "state.bin"
    neighbor = directory / "unrelated.bin"
    neighbor.write_bytes(b"untouched neighbor\x00\xff")
    committed = b"initial committed state\x00\xff"
    dest.write_bytes(committed)
    if os.name == "posix":
        dest.chmod(0o640)
    original_mode = stat.S_IMODE(dest.stat().st_mode)
    events = []
    real_replace = Path.replace
    real_unlink = Path.unlink
    real_os = fs.os
    for index, action in enumerate(actions):
        if record is not None:
            record({"event": "starting", "step": index, "action": action})
        proposed = index.to_bytes(4, "big") + bytes([index % 251]) * (65536 + index)
        boundary = _AtomicBoundary(action, directory)

        def replace(
            path: Path, target: Path, boundary: _AtomicBoundary = boundary
        ) -> Path:
            if boundary.action == "replace" and path.name.startswith(".state.bin."):
                boundary.hit("replace", boundary.primary)
            return real_replace(path, target)

        def unlink(
            path: Path,
            *,
            missing_ok: bool = False,
            boundary: _AtomicBoundary = boundary,
        ) -> None:
            if boundary.action == "write-unlink" and path.name.startswith(
                ".state.bin."
            ):
                boundary.hit(
                    "unlink", PermissionError(errno.EACCES, "secondary unlink")
                )
            real_unlink(path, missing_ok=missing_ok)

        caught = None
        try:
            fs.os = boundary  # type: ignore[assignment]
            Path.replace = replace  # type: ignore[method-assign]
            Path.unlink = unlink  # type: ignore[method-assign]
            try:
                fs.atomic_write(dest, proposed, durable=True)
            except OSError as error:
                caught = error
        finally:
            fs.os = real_os
            Path.replace = real_replace  # type: ignore[method-assign]
            Path.unlink = real_unlink  # type: ignore[method-assign]

        rollback = action not in {
            "success",
            "directory-open",
            "directory-sync",
            "directory-close",
        }
        if rollback:
            assert caught is boundary.primary, "operation exception was lost"
            assert dest.read_bytes() == committed, "destination changed before commit"
        else:
            assert caught is None, "best-effort directory durability became fatal"
            committed = proposed
            assert dest.read_bytes() == committed, "committed data not visible"

        expected_hits = {
            "success": [],
            "write-enospc": ["write"],
            "write-close": ["write", "stream-close"],
            "write-unlink": ["write", "unlink"],
        }.get(action, [action])
        assert boundary.hits == expected_hits, "scheduled boundary was not exercised"
        notes = getattr(caught, "__notes__", ())
        if action == "write-close":
            assert any("secondary close" in note for note in notes)
        if action == "write-unlink":
            assert any("secondary unlink" in note for note in notes)
        for fd in boundary.descriptors:
            closed_errno = None
            try:
                os.fstat(fd)
            except OSError as error:
                closed_errno = error.errno
            assert closed_errno == errno.EBADF, "scratch or directory descriptor leaked"
        scratch = list(directory.glob(".state.bin.*.tmp"))
        assert len(scratch) == (1 if action == "write-unlink" else 0)
        for orphan in scratch:
            assert orphan.read_bytes() == proposed[: len(proposed) // 2]
            assert any(str(orphan) in note for note in notes)
            orphan.unlink()
        assert stat.S_IMODE(dest.stat().st_mode) == original_mode, "file mode changed"
        assert neighbor.read_bytes() == b"untouched neighbor\x00\xff"
        events.append(
            {
                "step": index,
                "action": action,
                "hits": boundary.hits,
                "rollback": rollback,
                "committed_sha256": _digest(committed),
                "released_descriptors": len(boundary.descriptors),
                "orphan_count": len(scratch),
            }
        )

        # Every experiment must prove the same process remains usable afterwards.
        committed = b"recovered:" + proposed
        fs.atomic_write(dest, committed, durable=True)
        assert dest.read_bytes() == committed, "recovery write failed"
        assert stat.S_IMODE(dest.stat().st_mode) == original_mode
        assert sorted(p.name for p in directory.iterdir()) == [
            "state.bin",
            "unrelated.bin",
        ]
        if record is not None:
            record({"event": "completed", **events[-1], "recovered": True})
    return {"events": events, "final_sha256": _digest(committed)}


def strict_atomic_schedule(
    directory: Path,
    actions: tuple[str, ...],
    *,
    record: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Retain commit state across strict durability refusals and interruptions."""
    allowed = {
        "success",
        "preflight-fsync",
        "post-commit-fsync",
        "post-commit-cancel",
        "post-commit-fsync-close",
    }
    assert actions, "empty strict durability schedule"
    assert set(actions) <= allowed, "invalid strict durability schedule"
    dest = directory / "strict-state.bin"
    neighbor = directory / "strict-neighbor.bin"
    committed = b"initial acknowledged state"
    dest.write_bytes(committed)
    neighbor.write_bytes(b"unrelated state")
    real_os = fs.os
    events = []

    class Boundary(_AtomicBoundary):
        def __init__(self, action: str) -> None:
            super().__init__(action, directory)
            self.directory_syncs = 0
            self.failed_fd: int | None = None
            self.failure: BaseException = (
                KeyboardInterrupt() if action == "post-commit-cancel" else self.primary
            )

        def fsync(self, fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                self.directory_syncs += 1
                failure_at = 1 if self.action == "preflight-fsync" else 2
                if self.action != "success" and self.directory_syncs == failure_at:
                    self.failed_fd = fd
                    self.hits.append("directory-fsync")
                    raise self.failure
            os.fsync(fd)

        def close(self, fd: int) -> None:
            os.close(fd)
            if self.action == "post-commit-fsync-close" and fd == self.failed_fd:
                self.hits.append("directory-close")
                raise OSError(errno.EIO, "secondary strict directory close")

    for index, action in enumerate(actions):
        if record is not None:
            record({"event": "starting", "step": index, "action": action})
        proposed = b"strict generation:" + index.to_bytes(4, "big") * 16384
        boundary = Boundary(action)
        caught = None
        try:
            fs.os = boundary  # type: ignore[assignment]
            try:
                fs.atomic_write(dest, proposed, durable="strict")
            except (OSError, KeyboardInterrupt) as error:
                caught = error
        finally:
            fs.os = real_os
        if action == "success":
            assert caught is None, "successful strict write failed"
        else:
            assert caught is boundary.failure, "strict directory failure was hidden"
        committed_at_failure = action != "preflight-fsync"
        if committed_at_failure:
            committed = proposed
        assert dest.read_bytes() == committed, (
            "strict write changed its commit boundary"
        )
        notes = " ".join(getattr(caught, "__notes__", ()))
        if action.startswith("post-commit"):
            assert "replacement already committed" in notes
        else:
            assert "replacement already committed" not in notes
        if action == "post-commit-fsync-close":
            assert "secondary strict directory close" in notes
        assert boundary.directory_syncs == (1 if action == "preflight-fsync" else 2)
        for fd in boundary.descriptors:
            closed_errno = None
            try:
                os.fstat(fd)
            except OSError as error:
                closed_errno = error.errno
            assert closed_errno == errno.EBADF, (
                "strict write leaked an owned descriptor"
            )
        assert neighbor.read_bytes() == b"unrelated state"
        assert not list(directory.glob(".strict-state.bin.*.tmp"))
        committed = b"recovered strict generation:" + proposed
        fs.atomic_write(dest, committed, durable="strict")
        assert dest.read_bytes() == committed, "strict recovery failed"
        event = {
            "step": index,
            "action": action,
            "committed_at_failure": committed_at_failure,
            "directory_syncs": boundary.directory_syncs,
            "released_descriptors": len(boundary.descriptors),
            "hits": boundary.hits,
            "recovered": True,
        }
        events.append(event)
        if record is not None:
            record({"event": "completed", **event})
    return {"events": events, "final_sha256": _digest(committed)}


_PIPE_CHILD = r"""
from pathlib import Path
import os
import sys
import threading
mode = sys.argv[1]
Path('child-pid').write_text(str(os.getpid()))
if mode == 'early-stdin-close':
    os.close(0)
def emit(stream, byte):
    for _ in range(32):
        stream.write(byte * 16384)
        stream.flush()
threads = [threading.Thread(target=emit, args=(sys.stdout.buffer, b'O')),
           threading.Thread(target=emit, args=(sys.stderr.buffer, b'E'))]
for thread in threads:
    thread.start()
if mode != 'early-stdin-close':
    assert sys.stdin.buffer.read() == b'I' * (524288 + 17)
for thread in threads:
    thread.join()
Path('child-ready').write_text('ready')
if mode in {'timeout', 'interrupt'}:
    threading.Event().wait(30)
    Path('escaped-deadline').write_text('bad')
sys.exit(17 if mode == 'nonzero' else 0)
"""


def _assert_reaped(pid: int) -> None:
    if os.name == "posix":
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            message = "proc left a direct child unreaped"
            raise AssertionError(message)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            message = "proc left a direct child alive"
            raise AssertionError(message)


def process_schedule(
    directory: Path,
    actions: tuple[str, ...],
    *,
    record: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Exercise repeated pipe pressure, failures, timeout and interrupt recovery."""
    allowed = (
        {*PROCESS_ACTIONS, "interrupt"} if os.name == "posix" else set(PROCESS_ACTIONS)
    )
    assert actions, "empty process schedule"
    assert set(actions) <= allowed, "unsupported process schedule"
    events = []
    for index, action in enumerate(actions):
        if record is not None:
            record({"event": "starting", "step": index, "action": action})
        for name in ("child-pid", "child-ready"):
            (directory / name).unlink(missing_ok=True)
        logs = []
        interrupt_errors = []
        stop = threading.Event()
        interrupter = None
        if action == "interrupt":

            def interrupt(
                stop: threading.Event = stop, errors: list[str] = interrupt_errors
            ) -> None:
                deadline = time.monotonic() + 5
                while not stop.wait(0.005):
                    if (directory / "child-ready").exists():
                        os.kill(os.getpid(), signal.SIGINT)
                        return
                    if time.monotonic() >= deadline:
                        errors.append("child readiness deadline expired")
                        return

            interrupter = threading.Thread(target=interrupt, daemon=True)
            interrupter.start()
        caught = None
        result = None
        try:
            result = proc.run(
                [sys.executable, "-c", _PIPE_CHILD, action],
                cwd=directory,
                stdin_text="I" * (524288 + 17),
                timeout=3 if action == "timeout" else 8,
                log=logs.append,
            )
        except (proc.ProcessError, proc.TimeoutExpired, KeyboardInterrupt) as error:
            caught = error
        finally:
            stop.set()
            if interrupter is not None:
                interrupter.join(timeout=2)
                assert not interrupter.is_alive(), "interrupt helper did not stop"
        assert not interrupt_errors, interrupt_errors
        pid = int((directory / "child-pid").read_text())
        assert (directory / "child-ready").read_text() == "ready"
        assert not (directory / "escaped-deadline").exists()
        if action == "timeout":
            assert isinstance(caught, proc.TimeoutExpired), (
                "timeout accepted as completion"
            )
            assert caught.stdout == b"O" * 524288, "partial stdout lost"
            assert caught.stderr == b"E" * 524288, "partial stderr lost"
            assert logs == []
        elif action == "interrupt":
            assert isinstance(caught, KeyboardInterrupt), "cancellation was swallowed"
            assert logs == []
        else:
            if action == "nonzero":
                assert isinstance(caught, proc.ProcessError), (
                    "nonzero accepted as success"
                )
                result = caught.result
            else:
                assert caught is None, "successful child failed"
            assert result is not None
            assert result.returncode == (17 if action == "nonzero" else 0)
            assert result.stdout == "O" * 524288
            assert result.stderr == "E" * 524288, "stderr corruption accepted"
            assert len(logs) == 1
            assert logs[0]["returncode"] == result.returncode
        _assert_reaped(pid)
        assert (
            proc.capture([sys.executable, "-c", "print('recovered')"], timeout=5)
            == "recovered"
        )
        events.append(
            {
                "step": index,
                "action": action,
                "exception": type(caught).__name__ if caught is not None else None,
                "stdout_bytes": 524288,
                "stderr_bytes": 524288,
                "completion_logs": len(logs),
                "reaped": os.name == "posix",
                "recovery": True,
                "child_pid": pid,
            }
        )
        if record is not None:
            record({"event": "completed", **events[-1]})
    return {"events": events}
