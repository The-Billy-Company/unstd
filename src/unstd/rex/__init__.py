r"""Drop-in linear-time regex — irregex under a stdlib-``re``-faithful surface.

Stdlib ``re`` is a backtracking engine: a hostile pattern (or a hostile input
against a benign pattern) can trigger *catastrophic backtracking* — exponential
match time that pins a CPU (a ReDoS). That is unacceptable when the pattern or
the text is **untrusted** — LLM-supplied regexes, user filters, matching over
tool output or retrieved documents. ``rex`` is a stdlib-``re``-faithful surface
backed by **irregex**, our own finite-automaton engine, which matches in
time linear in the length of the text and independent of the pattern's shape —
the ReDoS class simply cannot occur on the irgx path. Migrate a call site by
swapping the import::

    import re  # →
    from unstd import rex  # rex.compile / rex.search / … , same surface

Backend & fallback: the ``rex`` extra provides ``irregex`` (import
name ``irgx``). Its default grammar excludes the two features that force
backtracking — **backreferences** (``\\1``) and **lookaround** (``(?=…)`` /
``(?<=…)``) — so it *declines* them with ``irgx.UnsupportedPattern`` rather
than failing. ``rex`` degrades gracefully in two ways, mirroring how
``serde.jsonx`` auto-falls-back for exact-format cases:

1. **Backend absent** (a base ``unstd`` install) → every call uses stdlib ``re``.
2. **Backend present but the pattern needs a PCRE-only feature** → irgx declines
   it at compile; ``rex`` transparently re-compiles that one pattern on stdlib
   ``re``. Also degrades for the two ``re`` flags irgx has no spelling for
   (``LOCALE`` / ``DEBUG``).

**The linear-time (ReDoS-immune) guarantee holds only on the irgx path.** A
pattern that degrades to stdlib ``re`` — a backref, a lookaround, ``LOCALE`` /
``DEBUG``, or the absent-backend case — silently loses the guarantee and is once
again backtracking. This is a deliberate trade (faithful behavior over a hard
failure); it is pinned as a regression test in ``tests/rex/test_rex.py``. If
linear-time is a *hard* requirement for a given call, keep the pattern within
irgx's default grammar (no backref/lookaround) and install the extra.

Faithfulness contract — what stays identical to stdlib ``re``:

- The module surface — ``compile`` · ``search`` · ``match`` · ``fullmatch`` ·
  ``findall`` · ``finditer`` · ``sub`` · ``subn`` · ``split`` · ``escape`` ·
  ``purge`` — plus the flag/type vocabulary (``I``/``IGNORECASE`` … ``Pattern``,
  ``Match``, ``RegexFlag``). ``escape`` is stdlib ``re.escape`` verbatim (its
  output is a literal irgx also accepts).
- ``error`` is re-exported from stdlib ``re``. ``irgx.error`` never escapes: a
  pattern irgx refuses — malformed or merely declined — is re-compiled on stdlib
  ``re``, which raises ``re.error`` for the malformed case, so ``except
  rex.error`` keeps catching every compile failure.
- Compiled objects and match objects: irgx's ``Pattern`` / ``Match`` mirror
  ``re.Pattern`` / ``re.Match`` (``.search``/``.match``/``.sub``/``.groups``/
  ``.groupdict``/…), so a compiled pattern is drop-in on either backend.
- Flags are honored by translating ``I``/``M``/``S``/``X`` into a leading inline
  ``(?imsx)`` group, and ``A`` into the same group's negated half (``-u``), since
  irgx takes no ``flags=`` argument. ``A`` and ``(?-u)`` agree exactly: both
  restrict ``\\w \\d \\s \\b`` and ``.`` to ASCII.
- ``bytes`` patterns ride the irgx path, compiled with ``(?-u)`` so classes are
  byte-oriented — which *is* stdlib's rule for a ``bytes`` pattern. Without that
  negation irgx would read the subject as UTF-8 and ``\\w+`` would swallow
  ``café``'s tail where stdlib stops at ``caf``.
- ``findall``'s non-participating groups are rewritten from irgx's ``None`` to
  stdlib's ``""`` (``.groups()`` already reports ``None`` in both libraries, so
  only this one projection needed adapting).
- ``finditer`` is handed back through ``iter()``: irgx answers the whole match
  sequence eagerly, and stdlib's contract is an *iterator*, so a caller holding
  one and calling ``next()`` behaves the same either way.

Divergences (deliberate, documented, pinned as tests):

- **PCRE-only patterns degrade to stdlib** (backref / lookaround) — losing the
  linear-time guarantee (above). ``compile`` returns a stdlib ``re.Pattern`` for
  these even when the backend is present.
- **``$`` is the end of the text, not the byte before a trailing newline.**
  Stdlib inherits Perl's rule where ``$`` also matches just before a final
  ``\\n``; irgx follows Rust's ``regex`` and Go's ``regexp`` in reading it as the
  absolute end. So ``rex.findall(r"[a-z]+$", "cat\\ndog\\n")`` finds nothing
  where stdlib finds ``dog``. Three spellings agree, and one of them is usually
  what was meant: ``(?m)[a-z]+$`` (every line's end), ``[a-z]+\\Z`` (the text's
  end, said exactly), or strip the newline first. **This divergence predates the
  irgx backend** — RE2 read ``$`` the same way — it was simply never written down.
- **POSIX bracket expressions mean different things.** ``[[:alpha:]]`` is a real
  character class to irgx and a nested-set typo to stdlib (which reads it as
  ``[`` ``:`` ``a`` ``l`` ``p`` ``h`` and warns). Also inherited from the RE2
  era, and the reason to keep patterns inside the shared grammar.
- **On the irgx path a compiled pattern's ``.pattern`` carries the inline
  ``(?imsx-u)`` prefix** we prepend, and ``.flags`` reflects irgx's own
  bookkeeping rather than the stdlib bitmask. Match *behavior* is identical;
  only these cosmetic attributes differ from what stdlib would report.
- **``re.UNICODE`` is a no-op on the irgx path** (irgx is UTF-8-native for
  ``str`` input). It is *not* mapped to inline ``(?U)`` — there ``(?U)`` swaps
  greediness, a different meaning entirely.

Prior art: the linear-time automaton construction is Thompson's, popularized for
this use by Russ Cox, "Regular Expression Matching Can Be Simple And Fast"
(2007, https://swtch.com/~rsc/regexp/regexp1.html). ``rex`` rode Google's RE2
implementation of it until the engine we publish ourselves — irregex
(https://github.com/The-Billy-Company/irregex) — grew the ``re``-shaped surface
this shim needs, at which point paying a third-party dependency for a capability
we ship stopped making sense.
"""

from __future__ import annotations

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
    purge,
)
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    import irgx as _irgx

    _HAVE_IRGX = True

    # irgx's compiled/match types (they deliberately mirror `re.Pattern` /
    # `re.Match`) — named here so the public unions below stay honest without
    # pretending the irgx objects nominally *are* the stdlib classes.
    type _IrgxPattern = _irgx.Pattern
    type _IrgxMatch = _irgx.Match
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

# irgx takes no `flags=` argument, so a bitmask is translated into one leading
# inline group. `i`/`m`/`s`/`x` are the positive half; ASCII is the negation of
# irgx's default Unicode mode, so it lands in the same group's `-u` half. NB we
# deliberately do NOT map re.UNICODE to `(?U)`: there `(?U)` swaps greediness.
_INLINE_FLAGS: tuple[tuple[int, str], ...] = (
    (IGNORECASE, "i"),
    (MULTILINE, "m"),
    (DOTALL, "s"),
    (VERBOSE, "x"),
)

# Flags irgx can honor without changing semantics: the four inline-mappable ones,
# ASCII (via `-u`), plus UNICODE (irgx's default for `str`, so a no-op) and
# NOFLAG (0). Only LOCALE and DEBUG carry semantics irgx has no spelling for, so
# a pattern that sets either degrades to stdlib `re`.
_HONORED = IGNORECASE | MULTILINE | DOTALL | VERBOSE | ASCII | UNICODE


def _inline_prefix(flags: int, *, ascii_classes: bool) -> str:
    """Render `flags` as irgx's leading inline group, or ``""`` if none apply."""
    on = "".join(token for bit, token in _INLINE_FLAGS if flags & bit)
    off = "u" if ascii_classes else ""
    return f"(?{on}-{off})" if off else f"(?{on})" if on else ""


def _irgx_pattern(pattern: str | bytes, flags: int) -> _IrgxPattern | None:
    """Compile `pattern` on irgx when it can faithfully express it; else ``None``.

    ``None`` (→ stdlib ``re`` fallback) when the backend is absent, `flags`
    carries a bit irgx can't honor, or irgx refuses the pattern — either
    declining it as outside the linear grammar (backreferences / lookaround) or
    rejecting it as malformed. Both refusals route to stdlib, which re-raises the
    malformed case as ``re.error``.
    """
    if not _HAVE_IRGX or flags & ~_HONORED:
        return None
    if isinstance(pattern, bytes):
        # A stdlib `bytes` pattern is byte-oriented by definition, which is
        # exactly what `-u` asks irgx for.
        pattern = _inline_prefix(flags, ascii_classes=True).encode() + pattern
    elif isinstance(pattern, str):
        # A `str` pattern only wants byte semantics under `re.ASCII`.
        pattern = _inline_prefix(flags, ascii_classes=bool(flags & ASCII)) + pattern
    else:
        # irgx accepts any buffer as a pattern; stdlib `re` accepts only `str`
        # and `bytes`. Decline so the `re` arm raises its own ``TypeError``
        # rather than this surface quietly compiling what its twin refuses.
        return None
    try:
        return _irgx.compile(pattern)
    except _irgx.error:
        return None


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
    pattern: AnyStr | Pattern[AnyStr], flags: int = 0
) -> Pattern[AnyStr] | _IrgxPattern:
    """Compile a regex — irgx (linear-time) where it can, else stdlib ``re``.

    Faithful to ``re.compile``: an already-compiled pattern is returned as-is,
    and passing `flags` alongside one raises the same ``ValueError`` stdlib does.
    """
    if isinstance(pattern, _re.Pattern):
        if flags:
            msg = "cannot process flags argument with a compiled pattern"
            raise ValueError(msg)
        return pattern
    rx = _irgx_pattern(pattern, flags)
    return rx if rx is not None else _re.compile(pattern, flags)


def search[AnyStr: (str, bytes)](
    pattern: AnyStr, string: AnyStr, flags: int = 0
) -> Match[AnyStr] | _IrgxMatch | None:
    """Scan `string` for the first location `pattern` matches (``re.search`` twin)."""
    return compile(pattern, flags).search(string)


def match[AnyStr: (str, bytes)](
    pattern: AnyStr, string: AnyStr, flags: int = 0
) -> Match[AnyStr] | _IrgxMatch | None:
    """Match `pattern` at the start of `string` (``re.match`` twin)."""
    return compile(pattern, flags).match(string)


def fullmatch[AnyStr: (str, bytes)](
    pattern: AnyStr, string: AnyStr, flags: int = 0
) -> Match[AnyStr] | _IrgxMatch | None:
    """Match `pattern` against the whole of `string` (``re.fullmatch`` twin)."""
    return compile(pattern, flags).fullmatch(string)


def findall[AnyStr: (str, bytes)](
    pattern: AnyStr, string: AnyStr, flags: int = 0
) -> list[AnyStr | tuple[AnyStr, ...]]:
    """Return every non-overlapping match of `pattern` in `string` (``re.findall`` twin).

    One list whose element is a string when the pattern has at most one group
    and a tuple when it has more — the same shape stdlib produces, spelled as a
    union because the choice is the pattern's, not the caller's. (Typeshed
    spells the same fact ``list[Any]``; this keeps the element types.)
    """
    rx = compile(pattern, flags)
    if isinstance(rx, _re.Pattern):
        # stdlib already projects an absent group as ``""``, so this arm needs
        # only the wider element type — a shallow copy, not a row rewrite.
        widened: list[AnyStr | tuple[AnyStr, ...]] = list(rx.findall(string))
        return widened
    return _absent_as_empty(rx.findall(string), string[:0])


def finditer[AnyStr: (str, bytes)](
    pattern: AnyStr, string: AnyStr, flags: int = 0
) -> Iterator[Match[AnyStr] | _IrgxMatch]:
    """Iterate over non-overlapping matches of `pattern` in `string` (``re.finditer`` twin).

    ``iter`` because irgx answers the whole sequence eagerly while stdlib hands
    back a lazy iterator; wrapping makes ``next()`` behave the same either way
    (and is a no-op on the stdlib path, where the value already is an iterator).
    """
    return iter(compile(pattern, flags).finditer(string))


def split[AnyStr: (str, bytes)](
    pattern: AnyStr, string: AnyStr, maxsplit: int = 0, flags: int = 0
) -> list[AnyStr | None]:
    """Split `string` by the occurrences of `pattern` (``re.split`` twin)."""
    return compile(pattern, flags).split(string, maxsplit)


def sub[AnyStr: (str, bytes)](
    pattern: AnyStr,
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
    pattern: AnyStr,
    repl: AnyStr | Callable[[Match[AnyStr] | _IrgxMatch], AnyStr],
    string: AnyStr,
    count: int = 0,
    flags: int = 0,
) -> tuple[AnyStr, int]:
    """Like :func:`sub`, but return ``(new_string, number_of_subs_made)`` (``re.subn`` twin)."""
    return compile(pattern, flags).subn(repl, string, count)
