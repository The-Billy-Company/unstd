"""Private POSIX process creation and captured-pipe kernels."""

import os
from types import NotImplementedType
from typing import Any

def spawn(
    child: Any,
    argv: tuple[str, ...],
    env: dict[str, str] | None,
    cwd: str | bytes | os.PathLike[str] | None,
    executable: str,
    p2cread: int,
    p2cwrite: int,
    c2pread: int,
    c2pwrite: int,
    errread: int,
    errwrite: int,
    restore_signals: bool,
) -> int | NotImplementedType: ...
def communicate(
    stdout_fd: int,
    stderr_fd: int,
    deadline: float,
) -> tuple[bytes, bytes, bool, bool, bool, bool, bool] | NotImplementedType: ...
