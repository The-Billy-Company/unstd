"""Incremental framing for a top-level JSON array, with one decode per item."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, NoReturn

from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from unstd.serde.jsonx import _StreamReader


_TOKENS = re.compile(r'["\\\[\]{},]')
_NONSPACE = re.compile(r"[^ \t\r\n]")
_WHITESPACE = " \t\r\n"


def _invalid(message: str, document: str, position: int = 0) -> NoReturn:
    """Report the current framing window without retaining the whole source."""
    raise json.JSONDecodeError(message, document, position)


def items(
    fp: _StreamReader,
    size: int,
    decode: Callable[[str], Any],
    *,
    chunks: Iterator[str] | None = None,
    state: str = "root",
) -> Iterator[Any]:
    """Frame each item in one pass, retaining only its fragments and one chunk."""
    fragments: list[str] = []
    depth = 0
    quoted = False
    escaped = False
    padding = False
    source = jsonx._stream_chunks(fp, size) if chunks is None else chunks
    for text in source:
        offset = 0
        while offset < len(text):
            if state != "item":
                found = _NONSPACE.search(text, offset)
                if found is None:
                    break
                offset = found.start()
                if state == "tail":
                    _invalid("Extra data", text, offset)
                if state == "root":
                    if text[offset] != "[":
                        _invalid("Expecting a top-level JSON array", text, offset)
                    state = "first"
                    offset += 1
                    continue
                if text[offset] == "]":
                    if state != "first":
                        _invalid("Expecting value", text, offset)
                    state = "tail"
                    offset += 1
                    continue
                state = "item"

            start = offset
            # An escape at the previous chunk's end consumes exactly one actual
            # character, including letters that the structural regex skips.
            if escaped:
                offset += 1
                escaped = False
            boundary = None
            for found in _TOKENS.finditer(text, offset):
                position = found.start()
                symbol = found[0]
                if quoted:
                    if escaped:
                        if position == offset:
                            escaped = False
                            offset = position + 1
                            continue
                        escaped = False
                    if symbol == "\\":
                        escaped = True
                    elif symbol == '"':
                        quoted = False
                    offset = position + 1
                    continue
                if symbol == '"':
                    quoted = True
                elif symbol == "[" or symbol == "{":
                    depth += 1
                elif depth == 0 and (symbol == "," or symbol == "]"):
                    boundary = position
                    break
                elif symbol == "]" or symbol == "}":
                    depth -= 1
                offset = position + 1

            stop = len(text) if boundary is None else boundary
            fragment = text[start:stop]
            trailing = False
            if not quoted and depth == 0:
                stripped = fragment.rstrip(_WHITESPACE)
                trailing = len(stripped) != len(fragment)
                fragment = stripped
            if fragment:
                if padding:
                    # Preserve a separator: dropping it would turn invalid `1 2`
                    # into valid `12` when a whitespace run crosses chunks.
                    fragments.append(" ")
                fragments.append(fragment)
                padding = trailing
            else:
                padding = padding or trailing
            if boundary is None:
                # A non-token after the last backslash also consumes its escape.
                escaped = escaped and offset == len(text)
                break

            encoded = "".join(fragments)
            fragments.clear()
            value = decode(encoded)
            encoded = ""
            padding = False
            state = "tail" if text[boundary] == "]" else "value"
            offset = boundary + 1
            yield value
            del value
    if state != "tail":
        # An unfinished item has no array delimiter. Do not invoke user hooks
        # for it while constructing the framing error.
        encoded = "".join(fragments)
        _invalid("Unterminated JSON array", encoded, len(encoded))
