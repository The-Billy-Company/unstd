"""Restrict plain values to the TOML dialect supported by stdlib readers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
from typing import Any

from tomlkit.container import Container
from tomlkit.items import Item, KeyType, SingleKey, String, StringType, Trivia


# TOML 1.0 basic-string escapes; ESC falls in the control range as ``\u001b``.
_ESCAPES = str.maketrans(
    {chr(c): f"\\u{c:04x}" for c in (*range(0x20), 0x7F)}
    | {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}
    | {'"': '\\"', "\\": "\\\\"}
)
_MINUTE = timedelta(minutes=1)


def _common_mapping(value: Mapping[Any, Any]) -> Mapping[Any, Any]:
    if isinstance(value, Item | Container):
        for child in value.values():
            _common(child)
        return value
    return {
        SingleKey(key, KeyType.Basic, original=f'"{key.translate(_ESCAPES)}"')
        if isinstance(key, str) and "\x1b" in key
        else key: _common(child)
        for key, child in value.items()
    }


def _common(value: object) -> object:
    """*value* restricted to the TOML 1.0 dialect; tomlkit items keep their spelling."""
    if isinstance(value, datetime):
        if (offset := value.utcoffset()) is not None and offset % _MINUTE:
            msg = f"TOML offsets are whole minutes, not {offset}: {value!r}"
            raise ValueError(msg)
    elif isinstance(value, time):
        if value.utcoffset() is not None:
            msg = f"a TOML local time carries no UTC offset: {value!r}"
            raise ValueError(msg)
    elif isinstance(value, str):
        if "\x1b" in value and not isinstance(value, Item):
            return String(StringType.SLB, value, value.translate(_ESCAPES), Trivia())
    # Below the root tomlkit accepts only plain dicts; other mappings stay as given.
    elif isinstance(value, dict | Item | Container) and isinstance(value, Mapping):
        return _common_mapping(value)
    elif isinstance(value, list | tuple):
        if isinstance(value, Item):
            for child in value:
                _common(child)
            return value
        return [_common(child) for child in value]
    return value
