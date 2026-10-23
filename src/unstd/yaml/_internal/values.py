"""Validate native YAML values without expanding aliases or copying data."""

from __future__ import annotations

from collections.abc import Hashable, MutableSet
from datetime import date
from typing import TYPE_CHECKING, TypeGuard


if TYPE_CHECKING:
    from unstd.yaml import YamlValue


def _is_value(value: object) -> TypeGuard[YamlValue]:
    """Narrow the untyped backend boundary without copying or expanding aliases."""
    pending = [value]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if node is None or isinstance(node, str | int | float | bytes | date):
            continue
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(node, dict):
            if not all(isinstance(key, Hashable) for key in node):
                return False
            pending.extend(node.values())
        elif isinstance(node, list | tuple):
            pending.extend(node)
        elif isinstance(node, MutableSet):
            if not all(isinstance(item, Hashable) for item in node):
                return False
        else:
            return False
    return True
