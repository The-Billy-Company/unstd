"""Stdlib process I/O with kernel notifications for bounded POSIX waits.

``Popen.communicate`` already multiplexes captured pipes efficiently. Its final
bounded POSIX wait, however, polls ``waitpid`` with exponential sleeps even when
the child has just closed those pipes. Keep stdlib communication and ownership,
replacing only that wait with pidfd or kqueue readiness where supported.
"""

from __future__ import annotations

import os
import select
import subprocess  # nosec B404 # the private backend of the safe runner
import sys
import time
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType
    from typing import TextIO


_native: ModuleType | None
try:
    from unstd.proc import _proc_native as _native
except ImportError:
    _native = None


def _exit_ready(pid: int, deadline: float) -> bool | None:
    """Wait for exit readiness, or return ``None`` without native support.

    Readiness never reaps the child. ``Popen`` retains its waitpid lock and exit
    status handling, including the status of children terminated by signals.
    Each call owns and closes its notification descriptor, including on timeout
    or cancellation. Kernel/capability errors propagate to the stdlib fallback.
    """
    if sys.platform == "linux" and hasattr(os, "pidfd_open"):
        descriptor = os.pidfd_open(pid, 0)
        try:
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            return bool(poller.poll(max(0.0, deadline - time.monotonic()) * 1000))
        finally:
            os.close(descriptor)
    if sys.platform == "darwin" and hasattr(select, "kqueue"):
        queue = select.kqueue()
        try:
            queue.control(
                [
                    select.kevent(
                        pid,
                        filter=select.KQ_FILTER_PROC,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                        fflags=select.KQ_NOTE_EXIT,
                    )
                ],
                0,
                0,
            )
            return bool(queue.control(None, 1, max(0.0, deadline - time.monotonic())))
        finally:
            queue.close()
    return None


class Popen(subprocess.Popen[str]):
    """The stdlib text-mode child with an event-driven bounded wait.

    The private ``_wait`` seam preserves ``wait``/``communicate`` SIGINT handling
    and delegates actual reaping to stdlib. Windows and unavailable native
    facilities keep the stdlib implementation without changing global state.
    """

    _communication_started: bool
    _fileobj2output: dict[Any, list[bytes]]
    text_mode: bool | str | None

    def _execute_child(self, *arguments: Any) -> None:
        """Use isolated native spawning for the runner's ordinary POSIX shape."""
        standard_execute: Callable[..., None] = getattr(super(), "_execute_child")  # noqa: B009 # private stdlib method
        if _native is None or os.name != "posix" or len(arguments) != 23:
            standard_execute(*arguments)
            return
        (
            argv,
            executable,
            preexec,
            close_fds,
            pass_fds,
            cwd,
            env,
            startupinfo,
            creationflags,
            shell,
            p2cread,
            p2cwrite,
            c2pread,
            c2pwrite,
            errread,
            errwrite,
            restore_signals,
            gid,
            gids,
            uid,
            umask,
            new_session,
            process_group,
        ) = arguments
        if (
            not argv
            or type(argv) is not tuple
            or executable is not None
            or preexec is not None
            or not close_fds
            or pass_fds
            or startupinfo is not None
            or creationflags
            or shell
            or gid is not None
            or gids is not None
            or uid is not None
            or umask != -1
            or new_session
            or process_group != -1
        ):
            standard_execute(*arguments)
            return
        executable = argv[0]
        try:
            status = _native.spawn(
                self,
                argv,
                env,
                cwd,
                executable,
                p2cread,
                p2cwrite,
                c2pread,
                c2pwrite,
                errread,
                errwrite,
                restore_signals,
            )
            if status is NotImplemented:
                standard_execute(*arguments)
                return
            # The C call publishes pid and ownership before returning, so a
            # pending signal cannot lose an already-created child at this seam.
            close_pipes: Callable[..., None] = getattr(self, "_close_pipe_fds")  # noqa: B009 # private stdlib method
            close_pipes(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)
        except BaseException:
            if getattr(self, "_child_created", False):
                self.kill()
                self.wait()
            raise

    def _communicate(
        self, input: str | None, endtime: float | None, orig_timeout: float | None
    ) -> tuple[str | None, str | None]:
        """Drain captured output natively; retain stdlib stdin and retry paths."""
        standard_communicate: Callable[..., tuple[str | None, str | None]] = getattr(  # noqa: B009 # private stdlib method
            super(), "_communicate"
        )
        if (
            _native is None
            or os.name != "posix"
            or self.stdin is not None
            or input is not None
            or self._communication_started
            or endtime is None
            or orig_timeout is None
            or not self.text_mode
            or self.stdout is None
            or self.stderr is None
        ):
            return standard_communicate(input, endtime, orig_timeout)
        stdout_pipe = cast("TextIO", self.stdout)
        stderr_pipe = cast("TextIO", self.stderr)
        result = _native.communicate(
            self.stdout.fileno(), self.stderr.fileno(), endtime
        )
        if result is NotImplemented:
            return standard_communicate(input, endtime, orig_timeout)
        stdout, stderr, expired, out_seen, err_seen, out_closed, err_closed = result
        self._fileobj2output = {
            self.stdout: [stdout] if out_seen else [],
            self.stderr: [stderr] if err_seen else [],
        }
        if out_closed:
            self.stdout.close()
        if err_closed:
            self.stderr.close()
        if expired:
            raise subprocess.TimeoutExpired(
                self.args,
                orig_timeout,
                output=stdout if out_seen else None,
                stderr=stderr if err_seen else None,
            )
        try:
            self.wait(timeout=max(0.0, endtime - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            error.timeout = orig_timeout
            raise
        translate: Callable[..., str] = getattr(self, "_translate_newlines")  # noqa: B009 # private stdlib method
        return (
            translate(stdout, stdout_pipe.encoding, stdout_pipe.errors),
            translate(stderr, stderr_pipe.encoding, stderr_pipe.errors),
        )

    def _wait(self, timeout: float | None) -> int:
        """Wait for child termination without POSIX timeout polling sleeps."""
        # Typeshed intentionally describes only Popen's public methods.
        standard_wait: Callable[[float | None], int] = getattr(super(), "_wait")  # noqa: B009 # private method omitted by typeshed
        if timeout is None:
            return standard_wait(timeout)
        deadline = time.monotonic() + timeout
        status = self.poll()
        if status is not None:
            return status
        if timeout <= 0:
            raise subprocess.TimeoutExpired(self.args, timeout)
        try:
            ready = _exit_ready(self.pid, deadline)
        except OSError:
            # Old kernels, sandbox restrictions, descriptor exhaustion and the
            # exit-before-registration ESRCH race all retain stdlib semantics.
            # Setup time counts against the original deadline.
            return standard_wait(max(0.0, deadline - time.monotonic()))
        if ready is None:
            return standard_wait(max(0.0, deadline - time.monotonic()))
        if ready:
            return standard_wait(None)
        status = self.poll()
        if status is not None:
            return status
        raise subprocess.TimeoutExpired(self.args, timeout)
