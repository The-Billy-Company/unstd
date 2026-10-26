"""The original native libarchive-c extract namespace."""

from typing import Any

def __getattr__(name: str) -> Any: ...
