"""Prove native regex eligibility against the running Python compiler."""

from __future__ import annotations

import _sre
import re
import re._compiler as _compiler
import re._constants as op
import re._parser as _parser
from typing import Any
import warnings


__all__ = ()


_INLINE_FLAGS = re.compile(r"\(\?([aiLmsux]*)(?:-([imsx]+))?([:)])")
_POSIX_TOKEN = re.compile(r"\[:\^?[A-Za-z]+:\]|\[([.=])(?:\\.|[^\[\\])*?\1\]")


def _comment_end(source: str, index: int, terminator: str) -> int:
    """Skip comment tokens, including escaped terminators and physical newlines."""
    while index < len(source):
        char = source[index]
        index += 2 if char == "\\" else 1
        if char == terminator:
            break
    return index


def _faithful_classes(pattern: str | bytes, flags: int, *, faithful: bool) -> bool:
    """Decline native nested sets and operators that Python treats as literals."""
    source = pattern.decode("latin1") if isinstance(pattern, bytes) else pattern
    verbose = bool(flags & re.VERBOSE)
    scopes: list[bool] = []
    in_class = False
    first = 0
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            if char == "[":
                posix = _POSIX_TOKEN.match(source, index)
                if faithful or posix is None:
                    return False
                index = posix.end()
                continue
            if char in "&-|~" and source[index : index + 2] == char * 2:
                return False
            if char == "]" and index != first:
                in_class = False
        elif verbose and char == "#":
            index = _comment_end(source, index + 1, "\n")
            continue
        elif source.startswith("(?#", index):
            index = _comment_end(source, index + 3, ")")
            continue
        elif char == "(":
            scoped = _INLINE_FLAGS.match(source, index)
            if scoped is not None:
                added, removed, end = scoped.groups()
                if end == ":":
                    scopes.append(verbose)
                    verbose = (verbose or "x" in added) and "x" not in (removed or "")
                index = scoped.end()
                continue
            scopes.append(verbose)
        elif char == ")":
            verbose = scopes.pop()
        elif char == "[":
            in_class = True
            first = index + 1 + (source[index + 1 : index + 2] == "^")
        index += 1
    return True


def native_profile(
    pattern: str | bytes, flags: int, *, faithful: bool = False
) -> bool | None:
    """Return the ASCII-subject requirement, or decline unrepresentable semantics."""
    # The authoritative compile already emitted any syntax warnings.
    with warnings.catch_warnings(record=True):
        parsed = _parser.parse(pattern, flags)
    if parsed.getwidth()[0] == 0:
        return None
    byte_transport = isinstance(pattern, str) and bool(flags & re.ASCII)
    profile = _profile(parsed, parsed.state.flags, byte_transport, faithful)
    return (
        profile
        if profile is not None
        and _faithful_classes(pattern, parsed.state.flags, faithful=faithful)
        else None
    )


def _profile(
    nodes: Any, flags: int, byte_transport: bool, faithful: bool = False
) -> bool | None:
    unicode = bool(flags & re.UNICODE)
    ascii_subject = False
    for kind, value in nodes:
        child: bool | None = False
        if kind in (op.LITERAL, op.NOT_LITERAL):
            if (
                unicode
                and flags & re.IGNORECASE
                and (value > 127 or _sre.unicode_iscased(value))
            ):
                return None
            if byte_transport and (kind is op.NOT_LITERAL or value > 127):
                return None
        elif kind is op.IN:
            child = _profile(value, flags, byte_transport, faithful)
        elif kind is op.NEGATE:
            if byte_transport:
                return None
        elif kind is op.RANGE:
            if byte_transport and value[1] > 127:
                return None
            if (
                unicode
                and flags & re.IGNORECASE
                and (
                    value[1] > 127
                    or any(map(_sre.unicode_iscased, range(value[0], value[1] + 1)))
                )
            ):
                return None
        elif kind is op.CATEGORY:
            # The runtime UCD owns Unicode categories. Word/digit semantics agree
            # on ASCII subjects; Unicode whitespace differs even within ASCII.
            if value in (op.CATEGORY_SPACE, op.CATEGORY_NOT_SPACE):
                if unicode or (byte_transport and value is op.CATEGORY_NOT_SPACE):
                    return None
            elif value in (op.CATEGORY_DIGIT, op.CATEGORY_WORD):
                child = unicode
            elif value in (op.CATEGORY_NOT_DIGIT, op.CATEGORY_NOT_WORD):
                if byte_transport:
                    return None
                child = unicode
            else:
                return None
        elif kind is op.AT:
            if faithful and value is op.AT_END and not flags & re.MULTILINE:
                return None
            if value in (op.AT_BOUNDARY, op.AT_NON_BOUNDARY):
                child = unicode
            elif value not in (
                op.AT_BEGINNING,
                op.AT_BEGINNING_STRING,
                op.AT_END,
                op.AT_END_STRING,
            ):
                return None
        elif kind is op.ANY:
            if byte_transport:
                return None
        elif kind is op.SUBPATTERN:
            group, added, removed, body = value
            if group and body.getwidth()[0] == 0:
                return None
            child = _profile(
                body,
                _compiler._combine_flags(flags, added, removed),
                byte_transport,
                faithful,
            )
        elif kind is op.BRANCH:
            for branch in value[1]:
                branch_profile = _profile(branch, flags, byte_transport, faithful)
                if branch_profile is None:
                    return None
                ascii_subject |= branch_profile
        elif kind in (op.MAX_REPEAT, op.MIN_REPEAT):
            # Empty iterations have different capture and adjacent-match rules.
            if value[2].getwidth()[0] == 0:
                return None
            child = _profile(value[2], flags, byte_transport, faithful)
        else:
            return None
        if child is None:
            return None
        ascii_subject |= child
    return ascii_subject
