"""A forked UUID fallback must not inherit a vanished thread's locked counter.

Found by the controlled concurrency schedule: another parent thread owns the
counter lock at fork, so its inherited child copy can never be released. A
child alarm and an outer process-group deadline make that historical deadlock
a bounded failure; the owning regression also runs in ordinary pytest runs.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.support.concurrency.ids import BASE_MS, TAIL
from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork lock ownership")
def test_child_can_mint_after_fork_with_a_held_parent_counter_lock(
    tmp_path: Path,
) -> None:
    completed = run_python(
        "from tests.support.concurrency.ids import main; main()",
        ("fork", '{"seed":173}'),
        cwd=tmp_path,
        timeout=12,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["ready"] is True
    assert observed["status"] == 0, observed
    assert observed["holder_alive"] is False
    assert observed["parent"] == [BASE_MS, 91, True]
    assert observed["backend_before"] == observed["backend_after"]
    assert observed["child"]["before"] == [None, 0, False]
    assert observed["child"]["backend"].endswith("._stdlib_uuid7")
    values = [UUID(value).int for value in observed["child"]["values"]]
    assert values == [
        BASE_MS << 80 | 7 << 76 | counter << 64 | 2 << 62 | TAIL for counter in (19, 20)
    ]
