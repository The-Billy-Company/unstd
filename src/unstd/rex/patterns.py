"""Compiled-pattern adapters with Python semantics and linear-policy checks."""

from __future__ import annotations

from itertools import repeat
import re
import sys
from typing import TYPE_CHECKING, Any, NoReturn, override

from .profile import _faithful_classes as _faithful_classes
from .profile import native_profile as native_profile


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from irgx import Match as NativeMatch
    from irgx import Pattern as NativePattern


_SURROGATES = re.compile("[\ud800-\udfff]")


def _absent_as_empty[AnyStr: (str, bytes)](
    rows: Sequence[AnyStr | None] | Sequence[tuple[AnyStr | None, ...]], empty: AnyStr
) -> list[AnyStr | tuple[AnyStr, ...]]:
    """Rewrite irgx's ``None`` for a non-participating group to stdlib's ``""``.

    The only place the two libraries project an absent group differently:
    ``.groups()`` already reports ``None`` on both, so nothing else needs this.

    `empty` is the caller's own zero-length string (``string[:0]``), which is
    how a ``str`` corpus gets ``""`` and a ``bytes`` corpus ``b""`` without
    branching on the subject type.
    """
    return [
        tuple(empty if group is None else group for group in row)
        if isinstance(row, tuple)
        else empty
        if row is None
        else row
        for row in rows
    ]


class _LinearPattern[AnyStr: (str, bytes)]:
    """A native pattern with Python's identity, projections and UTF-8 recovery.

    Both compiled objects are owned here. Unencodable original subjects use
    Python before a native match or callback can expose lazy UTF-8 indexing.
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

    def _needs_python(self, string: AnyStr) -> bool:
        """Decline subjects whose lazy native match projections cannot encode."""
        if type(string) not in (str, bytes):
            return True
        if self._ascii and not string.isascii():
            return True
        # Region matching may encode only a slice, while a returned Match later
        # indexes the entire original string as UTF-8. Recover before exposing
        # that object, including when the surrogate lies outside the region.
        return (
            isinstance(string, str)
            and not string.isascii()
            and _SURROGATES.search(string) is not None
        )

    def _call(self, name: str, *args: Any) -> Any:
        if self._needs_python(args[0]):
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
        if self._needs_python(string):
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
        return _absent_as_empty(rows, self._python.pattern[:0])

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
            or self._needs_python(string)
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
            value: AnyStr | None = repl(match)
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


class LinearRegexError(ValueError):
    """A requested regex operation cannot keep Python semantics on the linear engine."""


class _StrictLinearPattern[AnyStr: (str, bytes)](_LinearPattern[AnyStr]):
    """A Python-faithful native pattern restricted to one linear match operation.

    Python's compiled object supplies metadata only. Subject validation and
    capture materialization finish before a match becomes observable. Iteration
    and replacement are refused because native suffix rescans can be quadratic.
    Native refusals become :class:`LinearRegexError`.
    """

    __slots__ = ()

    @override
    def __getattr__(self, name: str) -> Any:
        """Expose metadata without borrowing Python's ancillary matching methods."""
        if name in ("pattern", "flags", "groups", "groupindex"):
            return getattr(self._python, name)
        if name == "scanner":
            self._unsupported(name)
        msg = f"{type(self).__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    @property
    def requires_ascii(self) -> bool:
        """Whether Python's Unicode rules restrict this pattern to ASCII subjects."""
        return self._ascii

    @override
    def _needs_python(self, string: AnyStr) -> bool:
        """Refuse subjects that would otherwise select Python's matching engine."""
        needs_python = (
            self._ascii and not string.isascii()
            if type(string) in (str, bytes)
            else True
        )
        needs_python |= (
            isinstance(string, str) and _SURROGATES.search(string) is not None
        )
        if type(string) is not type(self._python.pattern) or needs_python:
            msg = (
                "the subject requires Python fallback; use an exact str/bytes "
                "of the pattern's type, without lone surrogates"
                + (" and containing only ASCII" if self._ascii else "")
            )
            raise LinearRegexError(msg)
        return False

    @staticmethod
    def _limit(value: int, name: str) -> None:
        """Restrict region bounds to native integer arguments."""
        if type(value) is not int or not -sys.maxsize - 1 <= value <= sys.maxsize:
            msg = f"{name} requires an exact platform-sized int in linear mode"
            raise LinearRegexError(msg)

    @staticmethod
    def _materialize(match: NativeMatch | None) -> None:
        """Resolve lazy captures before returning a match to application code."""
        if match is not None:
            match.groups()
            match.span()

    @override
    def _call(self, name: str, *args: Any) -> Any:
        if name not in ("search", "match", "fullmatch"):
            self._unsupported(name)
        self._needs_python(args[0])
        for value in args[1:]:
            self._limit(value, "region bound")
        try:
            result = getattr(self._native, name)(*args)
            self._materialize(result)
        except self._declines as exc:
            msg = f"native {name} cannot preserve the linear regex guarantee"
            raise LinearRegexError(msg) from exc
        else:
            return result

    @staticmethod
    def _unsupported(name: str) -> NoReturn:
        """Reject operations whose total work is not proven linear in the subject."""
        msg = (
            f"{name} is unavailable under the linear regex guarantee; "
            "use search, match or fullmatch"
        )
        raise LinearRegexError(msg)

    @override
    def finditer(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> Iterator[re.Match[AnyStr] | NativeMatch]:
        """Refuse iteration because repeated native searches can rescan suffixes."""
        self._unsupported("finditer")

    @override
    def findall(
        self, string: AnyStr, pos: int = 0, endpos: int | None = None
    ) -> list[AnyStr | tuple[AnyStr, ...]]:
        """Refuse materialized iteration because total work can be quadratic."""
        self._unsupported("findall")

    @override
    def split(self, string: AnyStr, maxsplit: int = 0) -> list[AnyStr | None]:
        """Refuse splitting because native match iteration can be quadratic."""
        self._unsupported("split")

    @override
    def subn(
        self,
        repl: AnyStr | Callable[[re.Match[AnyStr] | NativeMatch], AnyStr],
        string: AnyStr,
        count: int = 0,
    ) -> tuple[AnyStr, int]:
        """Refuse substitution before any matching or replacement callback."""
        self._unsupported("subn")

    @override
    def sub(
        self,
        repl: AnyStr | Callable[[re.Match[AnyStr] | NativeMatch], AnyStr],
        string: AnyStr,
        count: int = 0,
    ) -> AnyStr:
        """Refuse substitution before any matching or replacement callback."""
        self._unsupported("sub")


# Retain historical class identities for pickles and exception inspection.
LinearRegexError.__module__ = "unstd.rex._faithful"
_LinearPattern.__module__ = "unstd.rex._faithful"
_StrictLinearPattern.__module__ = "unstd.rex._faithful"
