"""Compatibility access to private JSON encode helpers."""

from typing import Any

from unstd.serde._internal import json_encode as _implementation


def __getattr__(name: str) -> Any:
    """Resolve historical helper names from their owning implementation."""
    return getattr(_implementation, name)


def __dir__() -> list[str]:
    """Include helper names for existing diagnostic tools."""
    return sorted(set(globals()) | set(dir(_implementation)))
