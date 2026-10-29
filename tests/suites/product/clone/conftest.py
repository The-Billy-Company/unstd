"""Capability gates for direct tests of versioned native clone internals."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def native_mixed_graph() -> None:
    """Keep private-ABI route assertions on their qualified Python minors."""
    pytest.importorskip("unstd.clone._clone_native")
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        pytest.skip(
            "native mixed graph and record ABI is qualified on CPython 3.12–3.14"
        )
