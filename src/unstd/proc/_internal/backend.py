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
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable


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
