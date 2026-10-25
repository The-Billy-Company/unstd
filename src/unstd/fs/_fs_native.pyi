"""Private Linux atomic-write acceleration, with pre-mutation decline."""

import os
from types import NotImplementedType
from typing import Literal

def atomic_write(
    path: str | os.PathLike[str],
    payload: str | bytes,
    *,
    durable: bool | Literal["strict"] = False,
) -> NotImplementedType | None: ...
