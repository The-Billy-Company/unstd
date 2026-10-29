"""Capabilities required only by private-ABI native benchmark qualification."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def native_mixed_graph() -> None:
    """Require the CPython layouts audited for native record continuations."""
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        pytest.skip("native mixed-graph layouts are qualified on CPython 3.12–3.14")
    pytest.importorskip("unstd.clone._clone_native")
