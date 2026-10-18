"""Missing optional capabilities skip locally and fail in required CI lanes."""

import importlib
from types import ModuleType

import pytest


_REQUIRED = False


def configure_backend_policy(*, required: bool) -> None:
    """Select the calling lane's capability admission policy."""
    global _REQUIRED
    _REQUIRED = required


def require_backend(module: str) -> ModuleType:
    """Import a real backend, with explicit optional-local versus required posture."""
    if _REQUIRED:
        return importlib.import_module(module)
    return pytest.importorskip(
        module, reason=f"optional local backend {module} is absent"
    )
