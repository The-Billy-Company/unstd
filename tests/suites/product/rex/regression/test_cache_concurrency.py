"""Duplicate oldest-key eviction must not grow the documented 512-entry cache.

The concurrency campaign first found this naturally with seed 0. The minimized
schedule pauses four cache misses after each has chosen the same oldest key.
The old check-then-evict-then-publish algorithm retains 515 entries. Every case
uses a fresh deadline-owned interpreter so a broken native cache cannot hang
the default regression suite.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("backend", ["active", "stdlib"])
@pytest.mark.parametrize("seed", [0, 173])
def test_duplicate_oldest_eviction_keeps_cache_bounded(
    backend: str, seed: int, tmp_path: Path
):
    environment = dict(os.environ)
    if backend == "stdlib":
        environment["UNSTD_CHAOS_BACKEND"] = "stdlib"
    code = (
        "from tests.support.chaos.journal import apply_backend_policy; apply_backend_policy(); "
        "from tests.support.concurrency.shared_state import main; main()"
    )
    result = run_python(
        code,
        ["rex_eviction", str(seed), "normal"],
        cwd=tmp_path,
        env=environment,
        timeout=12,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    observations = report["observations"]
    assert observations["entries"] == 512
    assert observations["duplicate_oldest_evictions"] == 4
    if backend == "stdlib":
        assert observations["native_backend"] is False
    assert len(report["events"]) <= 100
    assert any(event["event"] == "workers.joined" for event in report["events"])
