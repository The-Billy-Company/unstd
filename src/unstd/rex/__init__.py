r"""Linear regex where Python's semantics survive the native boundary.

Swap ``import re`` for ``from unstd import rex`` to keep the module verbs,
flags, compile errors and ordinary match interface. The optional ``rex`` extra
provides ``irregex`` (import ``irgx``); a base installation uses stdlib ``re``.

The compiler validates with Python first, then reads its parsed operations,
including scoped flags. Native execution stays available for supported regular
patterns, bytes patterns, ASCII classes and verbose mode. Unicode word/digit
classes and boundaries use native execution on ASCII subjects; non-ASCII input
uses the running interpreter's Unicode rules. Unicode whitespace and cased
Unicode IGNORECASE expressions use Python, because their native semantics differ
even on some ASCII input. This is version-independent: no Unicode table is
pinned to one Python release.

Patterns that can match empty text or repeat a nullable body use Python's
adjacent-match and capture rules. Backreferences, lookaround, unsupported parsed
operations and the LOCALE/DEBUG flags also fall back. A native operation that
cannot express a requested match shape uses its typed refusal to recover; a
lone-surrogate pattern, subject or replacement uses Python because UTF-8 cannot
encode it. Recovery occurs before a replacement callback starts; callback
exceptions propagate unchanged and callbacks are never retried.

**Only an operation that actually runs on irgx has the linear-time, ReDoS-immune
guarantee.** Every fallback uses Python's backtracking engine. Installing the
extra alone does not establish that guarantee, and native eligibility can depend
on the subject. Keep untrusted patterns inside the supported regular grammar
and use bytes or faithful ASCII expressions over encodable input when the
linear guarantee is required. ``(a+)+$`` retains its native safety win; Python
hangs on its failing tail, so the adverse test checks Python in a subprocess.

Compiled objects retain the caller's source and Python flag metadata, accept
region bounds and round-trip through the module verbs. Native match objects
supply ``group``/``groups``/``groupdict``/``span``/``expand``/``start``/``end``;
``.pos`` and ``.endpos`` remain stdlib-only. Callable replacements receive the
active backend's match. ``findall`` projects an absent group as ``""`` for both
compiled and module calls; ``groups()`` still reports ``None``. Negative
replacement/split limits follow Python's unchanged-input rule.

``error`` and ``escape`` are stdlib's own objects. Malformed/native-only syntax
raises Python's error, so ``except rex.error`` remains authoritative. A buffer
pattern is refused with ``TypeError`` as stdlib refuses it; compiled patterns
from either backend are accepted, but adding flags to one raises ``ValueError``.
Compiles, including declines, are cached at 512 entries, oldest out. Without
that front cache a repeated backreference would pay a native compile and refusal
every time. ``purge`` clears our cache and both engines' caches. UNICODE never
becomes inline ``(?U)``: that native spelling changes greediness.

Two inherited syntax differences remain deliberate on the native path:
``$`` means absolute end rather than also the position before a final newline
(use ``(?m)…$`` for line ends or ``…\Z`` for absolute end), and POSIX bracket
expressions are real native classes rather than Python's nested-set spelling.
Keep patterns inside the shared grammar for Python parity. Native match
``.re.flags`` also reflects native bookkeeping; the facade's ``.flags`` is
Python's bitmask.

Prior art: Thompson's finite automata, explained by Russ Cox at
https://swtch.com/~rsc/regexp/regexp1.html, and the engine we publish at
https://github.com/The-Billy-Company/irregex. The previous backend was RE2;
irregex supplied the existing re-shaped API without that extra dependency.
"""

from __future__ import annotations

from contextlib import suppress
from itertools import repeat
import re as _re
from re import (  # faithful re-export of the flag + type vocabulary
    ASCII,
    DEBUG,
    DOTALL,
    IGNORECASE,
    LOCALE,
    MULTILINE,
    NOFLAG,
    UNICODE,
    VERBOSE,
    A,
    I,
    L,
    M,
    Match,
    Pattern,
    RegexFlag,
    S,
    U,
    X,
    escape,
)
from typing import TYPE_CHECKING, Any, Final

from ._faithful import _LinearPattern, native_profile


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    import irgx as _irgx

    _HAVE_IRGX: Final = True

    # irgx's compiled/match types (they deliberately mirror `re.Pattern` /
    # `re.Match`) — named here so the public unions below stay honest without
    # pretending the irgx objects nominally *are* the stdlib classes.
    type _IrgxPattern = _irgx.Pattern | _LinearPattern[Any]
    type _IrgxMatch = _irgx.Match
    # What every module-level verb accepts as `pattern`: stdlib's own
    # `str | Pattern` union, widened by the compiled object this module returns.
    type _Source[AnyStr: (str, bytes)] = AnyStr | Pattern[AnyStr] | _IrgxPattern
else:
    try:
        import irgx as _irgx

        _HAVE_IRGX = True
    except ImportError:  # base install without the `rex` extra — stdlib `re` fallback
        _irgx = None
        _HAVE_IRGX = False


# stdlib `re` is the authoritative error surface: every irgx refusal is caught
# and re-compiled on `re`, which raises this for the malformed ones — so `except
# rex.error` catches every failure regardless of the active backend.
error = _re.error

__all__ = [
    "ASCII",
    "DEBUG",
    "DOTALL",
    "IGNORECASE",
    "LOCALE",
    "MULTILINE",
    "NOFLAG",
    "UNICODE",
    "VERBOSE",
    "A",
    "I",
    "L",
    "M",
    "Match",
    "Pattern",
    "RegexFlag",
    "S",
    "U",
    "X",
    "compile",
    "error",
    "escape",
    "findall",
    "finditer",
    "fullmatch",
    "match",
    "purge",
    "search",
    "split",
    "sub",
    "subn",
]

# Flags irgx can honor without changing semantics: the four with a keyword twin,
# ASCII (`unicode=False`), plus UNICODE (irgx's default for `str`, so a no-op) and
# NOFLAG (0). Only LOCALE and DEBUG carry semantics irgx has no spelling for, so
# a pattern that sets either degrades to stdlib `re`. NB re.UNICODE is deliberately
# NOT inline `(?U)`: there `(?U)` swaps greediness.
_HONORED = IGNORECASE | MULTILINE | DOTALL | VERBOSE | ASCII | UNICODE

# Front cache over both backends, keyed like stdlib's own `re._cache`. It is what
# makes a module-level call one dict lookup, and it is the only memory of a
# *declined* pattern: irgx caches what it compiles, not what it refuses, and a
# refusal costs a full compile attempt plus a raise (~1.8 ms). Bounded, oldest
# first, because the patterns this module exists for are untrusted.
_MAXCACHE = 512
_cache: dict[tuple[type, str | bytes, int], Pattern[Any] | _IrgxPattern] = {}


def _irgx_pattern(pattern: str | bytes, flags: int) -> _IrgxPattern | None:
    """Compile with Python validation and a parsed native fidelity profile."""
    if not _HAVE_IRGX or type(pattern) not in (str, bytes) or flags & ~_HONORED:
        return None
    python = _re.compile(pattern, flags)
    try:
        native = _irgx.compile(
            pattern,
            ignore_case=bool(flags & IGNORECASE),
            multiline=bool(flags & MULTILINE),
            dotall=bool(flags & DOTALL),
            verbose=bool(flags & VERBOSE),
            # A `bytes` pattern is byte-oriented by definition; a `str` one only under ASCII.
            unicode=isinstance(pattern, str) and not flags & ASCII,
        )
    except (_irgx.error, UnicodeEncodeError):
        return None
    profile = native_profile(pattern, flags)
    return (
        _LinearPattern(native, python, profile, _irgx.error)
        if profile is not None
        else None
    )


# The str/bytes type parameter below is spelled `AnyStr` (typeshed's name for this
# same constraint on `re`) and must NOT be shortened to `S`: this module re-exports
# stdlib's `re.S` flag, so a type parameter named `S` reads as that flag to tooling
# that doesn't model PEP 695 scoping. ruff's FURB167 then "fixes" the alias by
# rewriting the parameter to `DOTALL`, silently destroying every generic signature
# in the file — which is exactly how a released copy of this module ended up
# annotated `pattern: DOTALL | Pattern[DOTALL]`.
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


def compile[AnyStr: (str, bytes)](  # shadows the builtin on purpose: `re.compile` twin
    pattern: _Source[AnyStr], flags: int = 0
) -> Pattern[AnyStr] | _IrgxPattern:
    """Compile a regex — irgx (linear-time) where it can, else stdlib ``re``.

    Faithful to ``re.compile``: an already-compiled pattern — from either
    backend — is returned as-is, and passing `flags` alongside one raises the
    same ``ValueError`` stdlib does.
    """
    if isinstance(flags, RegexFlag):
        flags = flags.value  # enum arithmetic and hashing run in Python; an int's don't
    if isinstance(pattern, str | bytes):
        key = (type(pattern), pattern, flags)
        if (rx := _cache.get(key)) is not None:
            return rx
        rx = _irgx_pattern(pattern, flags)
        if rx is None:
            rx = _re.compile(pattern, flags)
        _cache[key] = rx
        while len(_cache) > _MAXCACHE:
            # Publish before trimming: concurrent misses can choose the same
            # oldest key. Retry a lost eviction rather than each publishing an
            # extra entry after a suppressed duplicate deletion. Purge can also
            # empty the cache between this size check and the iterator read.
            with suppress(StopIteration, RuntimeError, KeyError):
                del _cache[next(iter(_cache))]
        return rx
    if isinstance(pattern, _re.Pattern | _LinearPattern) or (
        _HAVE_IRGX and isinstance(pattern, _irgx.Pattern)
    ):
        if flags:
            msg = "cannot process flags argument with a compiled pattern"
            raise ValueError(msg)
        return pattern
    # A buffer or anything else stdlib refuses, refused in stdlib's own words.
    msg = "first argument must be string or compiled pattern"
    raise TypeError(msg)


def search[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr], string: AnyStr, flags: int = 0
) -> Match[AnyStr] | _IrgxMatch | None:
    """Scan `string` for the first location `pattern` matches (``re.search`` twin)."""
    return compile(pattern, flags).search(string)


def match[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr], string: AnyStr, flags: int = 0
) -> Match[AnyStr] | _IrgxMatch | None:
    """Match `pattern` at the start of `string` (``re.match`` twin)."""
    return compile(pattern, flags).match(string)


def fullmatch[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr], string: AnyStr, flags: int = 0
) -> Match[AnyStr] | _IrgxMatch | None:
    """Match `pattern` against the whole of `string` (``re.fullmatch`` twin)."""
    return compile(pattern, flags).fullmatch(string)


def findall[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr], string: AnyStr, flags: int = 0
) -> list[AnyStr | tuple[AnyStr, ...]]:
    """Return every non-overlapping match of `pattern` in `string` (``re.findall`` twin).

    One list whose element is a string when the pattern has at most one group
    and a tuple when it has more — the same shape stdlib produces, spelled as a
    union because the choice is the pattern's, not the caller's. (Typeshed
    spells the same fact ``list[Any]``; this keeps the element types.)
    """
    rx = compile(pattern, flags)
    if isinstance(rx, _re.Pattern | _LinearPattern):
        found: list[AnyStr | tuple[AnyStr, ...]] = rx.findall(string)
        return found  # stdlib already projects an absent group as ``""``
    rows = rx.findall(string)
    # Rewrite only when an absent group is actually there: the probe is a C-level
    # containment scan, several times cheaper than rebuilding every row.
    absent = (
        any(map(tuple.__contains__, rows, repeat(None)))
        if rx.groups > 1
        else None in rows
    )
    return _absent_as_empty(rows, string[:0]) if absent else rows


def finditer[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr], string: AnyStr, flags: int = 0
) -> Iterator[Match[AnyStr] | _IrgxMatch]:
    """Iterate over non-overlapping matches of `pattern` in `string` (``re.finditer`` twin)."""
    return compile(pattern, flags).finditer(string)


def split[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr], string: AnyStr, maxsplit: int = 0, flags: int = 0
) -> list[AnyStr | None]:
    """Split `string` by the occurrences of `pattern` (``re.split`` twin)."""
    return compile(pattern, flags).split(string, maxsplit)


def sub[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr],
    repl: AnyStr | Callable[[Match[AnyStr] | _IrgxMatch], AnyStr],
    string: AnyStr,
    count: int = 0,
    flags: int = 0,
) -> AnyStr:
    """Replace the leftmost non-overlapping occurrences of `pattern` (``re.sub`` twin).

    A *callable* `repl` is handed whichever match object the active backend
    produces — hence the union, the same honesty :func:`search` applies to its
    return. The two are interchangeable for everything a replacement function
    normally reads (``group`` / ``groups`` / ``groupdict`` / ``span`` /
    ``expand`` / ``start`` / ``end``); only ``.pos`` and ``.endpos`` are
    stdlib-only. A *template* `repl` is backend-agnostic.
    """
    return compile(pattern, flags).sub(repl, string, count)


def subn[AnyStr: (str, bytes)](
    pattern: _Source[AnyStr],
    repl: AnyStr | Callable[[Match[AnyStr] | _IrgxMatch], AnyStr],
    string: AnyStr,
    count: int = 0,
    flags: int = 0,
) -> tuple[AnyStr, int]:
    """Like :func:`sub`, but return ``(new_string, number_of_subs_made)`` (``re.subn`` twin)."""
    return compile(pattern, flags).subn(repl, string, count)


def purge() -> None:
    """Clear the regular expression caches (``re.purge`` twin) — ours and both backends'."""
    _cache.clear()
    _re.purge()
    if _HAVE_IRGX:
        _irgx.purge()
