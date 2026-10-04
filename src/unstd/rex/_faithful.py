"""Prove native semantics from Python's compiler, and own UTF-8 recovery.

The backend's Unicode tables describe its own language, not Python's. Only
compiler operations whose semantics survive that boundary may run natively.
Unknown operations decline; no spelling or substring of a pattern decides this.
"""

from __future__ import annotations

import _sre
from itertools import repeat
import re
import re._compiler as _compiler
import re._constants as op
import re._parser as _parser
import sys
from typing import TYPE_CHECKING, Any
import warnings


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from irgx import Match as NativeMatch
    from irgx import Pattern as NativePattern


def native_profile(pattern: str | bytes, flags: int) -> bool | None:
    """Return the ASCII-subject requirement, or decline unrepresentable semantics."""
    # The authoritative compile already emitted any syntax warnings.
    with warnings.catch_warnings(record=True):
        parsed = _parser.parse(pattern, flags)
    if parsed.getwidth()[0] == 0:
        return None
    byte_transport = isinstance(pattern, str) and bool(flags & re.ASCII)
    return _profile(parsed, parsed.state.flags, byte_transport)


def _profile(nodes: Any, flags: int, byte_transport: bool) -> bool | None:
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
            child = _profile(value, flags, byte_transport)
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
                body, _compiler._combine_flags(flags, added, removed), byte_transport
            )
        elif kind is op.BRANCH:
            for branch in value[1]:
                branch_profile = _profile(branch, flags, byte_transport)
                if branch_profile is None:
                    return None
                ascii_subject |= branch_profile
        elif kind in (op.MAX_REPEAT, op.MIN_REPEAT):
            # Empty iterations have different capture and adjacent-match rules.
            if value[2].getwidth()[0] == 0:
                return None
            child = _profile(value[2], flags, byte_transport)
        else:
            return None
        if child is None:
            return None
        ascii_subject |= child
    return ascii_subject


class _LinearPattern[AnyStr: (str, bytes)]:
    """A native pattern with Python's identity, projections and UTF-8 recovery.

    Both compiled objects are owned here. A UTF-8 refusal happens before a
    native match exists, so the same operation can run on the Python pattern.
    User callbacks are never retried after they have started.
    """

    __slots__ = ("_ascii", "_declines", "_native", "_python")

    def __init__(
        self,
        native: NativePattern,
        python: re.Pattern[AnyStr],
        ascii_subject: bool,
        refusal: type[Exception],
    ) -> None:
        self._ascii = ascii_subject
        self._declines = (UnicodeEncodeError, refusal)
        self._native = native
        self._python: re.Pattern[AnyStr] = python

    def __getattr__(self, name: str) -> Any:
        """Read pattern metadata and ancillary stdlib operations from Python."""
        return getattr(self._python, name)

    def _call(self, name: str, *args: Any) -> Any:
        if type(args[0]) not in (str, bytes) or (self._ascii and not args[0].isascii()):
            return getattr(self._python, name)(*args)
        try:
            return getattr(self._native, name)(*args)
        except self._declines:
            return getattr(self._python, name)(*args)

    def search(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> re.Match[AnyStr] | NativeMatch | None:
        """Search the original string, preserving Python's region bounds."""
        result: re.Match[AnyStr] | NativeMatch | None = (
            self._call("search", string, pos, endpos)
            if endpos is not None
            else self._call("search", string, pos)
        )
        return result

    def match(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> re.Match[AnyStr] | NativeMatch | None:
        """Match at the region start, preserving Python's region bounds."""
        result: re.Match[AnyStr] | NativeMatch | None = (
            self._call("match", string, pos, endpos)
            if endpos is not None
            else self._call("match", string, pos)
        )
        return result

    def fullmatch(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> re.Match[AnyStr] | NativeMatch | None:
        """Match the complete region, preserving Python's region bounds."""
        result: re.Match[AnyStr] | NativeMatch | None = (
            self._call("fullmatch", string, pos, endpos)
            if endpos is not None
            else self._call("fullmatch", string, pos)
        )
        return result

    def finditer(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> Iterator[re.Match[AnyStr] | NativeMatch]:
        """Iterate native matches, or Python matches after a UTF-8 refusal."""
        result: Iterator[re.Match[AnyStr] | NativeMatch] = (
            self._call("finditer", string, pos, endpos)
            if endpos is not None
            else self._call("finditer", string, pos)
        )
        return result

    def findall(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> list[AnyStr | tuple[AnyStr, ...]]:
        """Project absent groups as empty strings, including compiled calls."""
        if type(string) not in (str, bytes) or (self._ascii and not string.isascii()):
            result: list[AnyStr | tuple[AnyStr, ...]] = (
                self._python.findall(string, pos, endpos)
                if endpos is not None
                else self._python.findall(string, pos)
            )
            return result
        try:
            rows = (
                self._native.findall(string, pos, endpos)
                if endpos is not None
                else self._native.findall(string, pos)
            )
        except self._declines:
            fallback: list[AnyStr | tuple[AnyStr, ...]] = (
                self._python.findall(string, pos, endpos)
                if endpos is not None
                else self._python.findall(string, pos)
            )
            return fallback
        if not self._python.groups:
            whole: list[AnyStr | tuple[AnyStr, ...]] = rows
            return whole
        absent = (
            any(map(tuple.__contains__, rows, repeat(None)))
            if self._python.groups > 1
            else None in rows
        )
        if not absent:
            found: list[AnyStr | tuple[AnyStr, ...]] = rows
            return found
        empty = self._python.pattern[:0]
        return [
            tuple(empty if group is None else group for group in row)
            if isinstance(row, tuple)
            else empty
            if row is None
            else row
            for row in rows
        ]

    def split(self, string: AnyStr, maxsplit: int = 0) -> list[AnyStr | None]:
        """Split on matches with Python's group projection."""
        if (
            type(string) not in (str, bytes)
            or type(maxsplit) is not int
            or not 0 <= maxsplit <= sys.maxsize
        ):
            return self._python.split(string, maxsplit)
        limit = 0 if maxsplit and maxsplit > len(string) else maxsplit
        result: list[AnyStr | None] = self._call("split", string, limit)
        return result

    def subn(
        self,
        repl: AnyStr | Callable[[re.Match[AnyStr] | NativeMatch], AnyStr],
        string: AnyStr,
        count: int = 0,
    ) -> tuple[AnyStr, int]:
        """Replace matches, recovering only before a user callback starts."""
        if (
            type(count) is not int
            or not 0 <= count <= sys.maxsize
            or type(string) not in (str, bytes)
            or (self._ascii and not string.isascii())
        ):
            return self._python.subn(repl, string, count)
        limit = 0 if count and count > len(string) else count
        if not callable(repl):
            try:
                result: tuple[AnyStr, int] = self._native.subn(repl, string, limit)
            except self._declines:
                return self._python.subn(repl, string, count)
            else:
                return result
        started = False

        def replace(match: re.Match[AnyStr] | NativeMatch) -> AnyStr:
            nonlocal started
            started = True
            value = repl(match)
            return self._python.pattern[:0] if value is None else value

        try:
            result = self._native.subn(replace, string, limit)
        except self._declines:
            if started:
                raise
            return self._python.subn(repl, string, count)
        else:
            return result

    def sub(
        self,
        repl: AnyStr | Callable[[re.Match[AnyStr] | NativeMatch], AnyStr],
        string: AnyStr,
        count: int = 0,
    ) -> AnyStr:
        """Replace matches and return the resulting string."""
        return self.subn(repl, string, count)[0]
