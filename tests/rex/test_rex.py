"""Adversarial + parity tests for the linear-time regex surface (``unstd.rex``).

These pin the contract ``rex`` promises: it is a stdlib-``re``-faithful drop-in
whose *safety* win — linear-time, ReDoS-immune matching — holds on the irgx path,
and which transparently degrades to stdlib ``re`` (losing that guarantee) for the
PCRE-only features irgx's default grammar excludes (backreferences, lookaround),
for the two flags it has no spelling for (``LOCALE`` / ``DEBUG``), and when the
backend is absent.

Both backends are exercised: the parity + degrade tests run identically either
way, and the catastrophic-backtracking test branches on ``rex._HAVE_IRGX`` — it
*proves* the safety win when the backend is present (as it is in this env, via
the ``unstd[rex]`` extra) and asserts the documented degrade otherwise. A
subprocess proof that stdlib ``re`` genuinely blows up on the same pattern
anchors the "would hang" claim non-tautologically.

Three flag families ride the linear path that could not under the previous RE2
backend — ``VERBOSE``, ``ASCII``, and ``bytes`` patterns — so each gets a parity
test rather than a degrade test, and the ``bytes`` one is deliberately adverse:
it matches over non-ASCII input, which is the only way to catch the engine
reading the subject as UTF-8 when stdlib would read it as bytes.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import pytest

from unstd import rex


if TYPE_CHECKING:
    from collections.abc import Callable


# ── faithfulness: rex mirrors stdlib re on the shared (RE2-expressible) grammar ──

# Patterns valid in BOTH engines (no backref / lookaround), matched against text.
_PARITY: list[tuple[str, str]] = [
    (r"\d+", "abc123def456ghi"),
    (r"(\w)(\w)", "ab cd ef"),
    (r"foo|bar", "a bar then foo"),
    (r"[A-Za-z]+", "Hello, World!"),
    (r"a*", "aaabaa"),
    (r"(\d{2,4})-(\d{2})", "id 2026-07 and 99-01"),
    (r"\bword\b", "a word in words"),
    (r"h.llo", "hello hallo hxllo"),
]


@pytest.mark.parametrize(("pattern", "text"), _PARITY)
def test_findall_matches_stdlib_re(pattern: str, text: str) -> None:
    """Test findall matches stdlib re."""
    assert rex.findall(pattern, text) == re.findall(pattern, text)


@pytest.mark.parametrize(("pattern", "text"), _PARITY)
def test_search_span_and_groups_match_stdlib_re(pattern: str, text: str) -> None:
    """Test search span and groups match stdlib re."""
    rx_m, re_m = rex.search(pattern, text), re.search(pattern, text)
    assert (rx_m is None) == (re_m is None)
    if rx_m is not None and re_m is not None:
        assert rx_m.span() == re_m.span()
        assert rx_m.groups() == re_m.groups()
        assert rx_m.group(0) == re_m.group(0)


@pytest.mark.parametrize(("pattern", "text"), _PARITY)
def test_finditer_spans_match_stdlib_re(pattern: str, text: str) -> None:
    """Test finditer spans match stdlib re."""
    assert [m.span() for m in rex.finditer(pattern, text)] == [
        m.span() for m in re.finditer(pattern, text)
    ]


def test_flags_ignorecase_multiline_dotall_are_faithful() -> None:
    """Test flags ignorecase multiline dotall are faithful."""
    assert rex.search(r"hello", "HELLO", rex.IGNORECASE) is not None
    assert [m.group() for m in rex.finditer(r"^\w+", "ab\ncd\nef", rex.MULTILINE)] == [
        "ab",
        "cd",
        "ef",
    ]
    assert rex.search(r"a.b", "a\nb", rex.DOTALL) is not None
    assert rex.search(r"a.b", "a\nb") is None  # no DOTALL → dot ≠ newline


def test_sub_subn_split_are_faithful() -> None:
    """Test sub subn split are faithful."""
    assert rex.sub(r"(\w+)", r"[\1]", "hi yo") == re.sub(r"(\w+)", r"[\1]", "hi yo")
    assert rex.subn(r"\d", "#", "a1b2c3") == re.subn(r"\d", "#", "a1b2c3")
    assert rex.split(r"\s+", "a b  c\td") == re.split(r"\s+", "a b  c\td")
    # sub with a callable repl (exercises the Match object surface on either backend)
    assert rex.sub(r"\d", lambda m: str(int(m.group()) + 1), "a1b2") == "a2b3"


def test_escape_is_stdlib_re_escape() -> None:
    """Test escape is stdlib re escape."""
    assert rex.escape is re.escape
    hostile = r"a.b*c+(d)[e]{f}|g^h$i?"
    assert rex.search(rex.escape(hostile), f"x{hostile}y") is not None


def test_unicode_flag_does_not_flip_greediness() -> None:
    # Regression guard for the inline mapping: re.UNICODE must NOT become inline
    # `(?U)` (which swaps greediness in this lineage). `a+` stays greedy → full run.
    """Test unicode flag does not flip greediness."""
    hit = rex.search(r"a+", "aaa", rex.UNICODE)
    assert hit is not None
    assert hit.group() == "aaa"


def test_findall_reports_absent_group_as_empty_string_like_stdlib() -> None:
    """A non-participating group is ``""`` in ``findall`` and ``None`` in ``groups()``.

    irgx reports ``None`` in both; stdlib splits the two, and ``rex`` adapts the
    ``findall`` projection so the drop-in claim holds. Only that one projection
    needed it — ``.groups()`` already agrees on either backend.
    """
    assert rex.findall(r"(a)|(b)", "ab") == re.findall(r"(a)|(b)", "ab")
    assert rex.findall(rb"(a)|(b)", b"ab") == re.findall(rb"(a)|(b)", b"ab")
    mine, theirs = rex.search(r"(a)|(b)", "b"), re.search(r"(a)|(b)", "b")
    assert mine is not None
    assert theirs is not None
    assert mine.groups() == theirs.groups() == (None, "b")


# ── documented divergences: where the linear backend and stdlib disagree ────────


def test_dollar_anchor_is_absolute_end_not_before_trailing_newline() -> None:
    r"""``$`` does not match before a final ``\n`` — Rust/Go's rule, not Perl's.

    The one place ``rex`` and stdlib disagree about whether a pattern matches at
    all. Predates the irgx backend (RE2 read ``$`` the same way); pinned here so
    the divergence is a decision rather than a surprise, together with the three
    spellings that DO agree.
    """
    if not rex._HAVE_IRGX:
        pytest.skip("backend absent — the stdlib fallback has stdlib's own `$`")
    assert rex.findall(r"[a-z]+$", "cat\ndog\n") == []
    assert re.findall(r"[a-z]+$", "cat\ndog\n") == ["dog"]
    # The portable spellings, each agreeing with stdlib on the same subject.
    assert rex.findall(r"(?m)[a-z]+$", "cat\ndog\n") == re.findall(
        r"(?m)[a-z]+$", "cat\ndog\n"
    )
    assert rex.findall(r"[a-z]+\Z", "cat\ndog") == re.findall(r"[a-z]+\Z", "cat\ndog")
    assert rex.findall(r"[a-z]+$", "cat\ndog") == re.findall(r"[a-z]+$", "cat\ndog")


# ── error surface: rex.error catches every compile failure on either backend ─────


def test_error_is_stdlib_re_error_and_catches_bad_patterns() -> None:
    """Test error is stdlib re error and catches bad patterns."""
    assert rex.error is re.error
    with pytest.raises(rex.error):
        rex.compile("(")  # unbalanced group — malformed in both engines
    with pytest.raises(rex.error):
        rex.compile("[")  # unterminated character class


# ── documented degrade: PCRE-only patterns fall back to stdlib re ────────────────
# `compile` returning a stdlib re.Pattern is the pin — on the linear path a
# supported pattern returns irgx's own object, so an re.Pattern here proves the
# fallback fired (and thus that the linear-time guarantee is knowingly forgone).


def test_backreference_degrades_to_stdlib_re() -> None:
    """Test backreference degrades to stdlib re."""
    rx = rex.compile(r"(\w+)\s+\1")
    assert isinstance(rx, re.Pattern)  # outside the linear grammar → stdlib fallback
    hit = rx.search("hi hi")
    assert hit is not None
    assert hit.group(1) == "hi"
    assert rx.search("hi yo") is None
    if rex._HAVE_IRGX:  # and when the backend IS present, irgx truly declined it
        assert rex._irgx_pattern(r"(\w+)\s+\1", 0) is None


def test_lookaround_degrades_to_stdlib_re() -> None:
    """Test lookaround degrades to stdlib re."""
    rx = rex.compile(r"foo(?=bar)")
    assert isinstance(rx, re.Pattern)  # lookahead is PCRE-only → stdlib fallback
    assert rx.search("foobar") is not None
    assert rx.search("foobaz") is None
    if rex._HAVE_IRGX:
        assert rex._irgx_pattern(r"foo(?=bar)", 0) is None
        assert rex._irgx_pattern(r"(?<=x)y", 0) is None  # lookbehind too


def test_unhonored_flag_degrades_to_stdlib_re() -> None:
    # LOCALE and DEBUG are the only two flags irgx has no spelling for, so they
    # are what the degrade path is now for. Still faithful, just backtracking.
    """Test unhonored flag degrades to stdlib re."""
    rx = rex.compile(rb"\w+", rex.LOCALE)
    assert isinstance(rx, re.Pattern)
    assert rx.findall(b"ab cd") == [b"ab", b"cd"]


# ── widened linear path: three families RE2 could not take, and irgx can ─────────
# Each is a PARITY test, not a degrade test — the point is that the guarantee now
# covers them. `not isinstance(..., re.Pattern)` is the pin that we are on irgx.


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="backend absent — everything degrades")
def test_verbose_rides_the_linear_path_faithfully() -> None:
    """Test verbose rides the linear path faithfully."""
    rx = rex.compile(r"a b   c", rex.VERBOSE)
    assert not isinstance(rx, re.Pattern)
    assert rx.search("abc") is not None  # verbose mode ignores unescaped whitespace
    # Trivia inside a character class is NOT stripped — stdlib's rule, not rg's.
    assert rex.findall(r"(?x)[a b]", "a b") == re.findall(r"(?x)[a b]", "a b")


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="backend absent — everything degrades")
def test_ascii_flag_rides_the_linear_path_faithfully() -> None:
    """Test ascii flag rides the linear path faithfully."""
    rx = rex.compile(r"\w+", rex.ASCII)
    assert not isinstance(rx, re.Pattern)
    # ASCII must restrict the classes, not merely be accepted: `é` is not a word char.
    assert (
        rex.findall(r"\w+", "café", rex.ASCII)
        == re.findall(r"\w+", "café", re.ASCII)
        == ["caf"]
    )
    # …and the spans stay codepoint-indexed, so a caller can still slice with them.
    text = "naïve café"
    assert all(
        text[m.start() : m.end()] == m.group()
        for m in rex.finditer(r"\w+", text, rex.ASCII)
    )


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="backend absent — everything degrades")
def test_bytes_pattern_rides_the_linear_path_byte_oriented() -> None:
    r"""A `bytes` pattern is byte-oriented in stdlib, and must stay so on irgx.

    Adverse by construction: irgx defaults to reading a subject as UTF-8, so a
    naive swap makes ``\w+`` swallow ``café`` whole where stdlib stops at ``caf``.
    ASCII-only input cannot catch that, which is why every case here is non-ASCII.
    """
    rx = rex.compile(rb"\d+")
    assert not isinstance(rx, re.Pattern)
    assert rx.findall(b"a1b22c333") == [b"1", b"22", b"333"]
    for pattern, subject in ((rb"\w+", "café".encode()), (rb".", "café".encode())):
        assert rex.findall(pattern, subject) == re.findall(pattern, subject)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="backend absent — everything degrades")
@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (r"\u00e9", "café"),  # braced-less \uHHHH — stdlib spells it, rg does not
        (r"[\u00ab-\u00bb]", "x\u00acy"),  # …and it may bound a class range
        (r"\N{SNOWMAN}", "a\u2603b"),  # by Unicode name
        (r"\0101", "\x081"),  # octal: \010 then a literal '1', per stdlib's rule
        (r"[\1]", "\x01"),  # inside a class every numeric escape is octal
    ],
)
def test_by_value_escapes_match_stdlib_re_on_the_linear_path(
    pattern: str, text: str
) -> None:
    r"""Character-by-value escapes are stdlib-faithful *and* stay linear.

    This is the fidelity the ``irregex>=2.3.0`` floor buys: before it, these
    spellings were absent from the linear arm, so a stdlib pattern using one
    either lost the guarantee or was refused. Positional octal is the subtle half
    — stdlib reads ``\1`` as a group reference at atom position but as octal
    inside ``[…]``, and both readings are pinned here.
    """
    assert not isinstance(rex.compile(pattern), re.Pattern)
    assert rex.findall(pattern, text) == re.findall(pattern, text)
    assert bool(rex.search(pattern, text)) is bool(re.search(pattern, text)) is True


@pytest.mark.parametrize("pattern", [bytearray(rb"\d+"), memoryview(rb"\d+")])
def test_buffer_pattern_is_refused_exactly_as_stdlib_re_refuses_it(
    pattern: bytearray | memoryview,
) -> None:
    """A pattern stdlib won't take, this surface must not take either.

    Adverse direction: irgx accepts *any* buffer as a pattern, so the faithful
    behavior is the narrower one. Being a superset of the twin is a divergence
    too — code that compiled here would raise the moment the backend was absent.
    The ignores are the assertion: passing a type the annotation forbids is
    exactly what is under test.
    """
    with pytest.raises(TypeError):
        re.compile(pattern)  # type: ignore[call-overload]
    with pytest.raises(TypeError):
        rex.compile(pattern)  # type: ignore[type-var]


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="backend absent — everything degrades")
def test_callable_repl_sees_a_match_interchangeable_with_stdlib_re() -> None:
    """A replacement *function* is handed the active backend's match object.

    ``sub``/``subn`` type `repl` as taking either match because on the linear
    path it really is irgx's. That is only tolerable while the two are
    interchangeable for what a replacement function reads, so pin the surface
    here rather than trusting the annotation.
    """
    read = ("group", "groups", "groupdict", "start", "end", "span", "expand")
    seen: list[rex.Match[str] | rex._IrgxMatch] = []

    def repl(m: rex.Match[str] | rex._IrgxMatch) -> str:
        seen.append(m)
        return m.group(2) + m.group(1)

    pattern, subject = r"(\w+)-(\d+)", "ab-12 cd-34"
    assert not isinstance(rex.compile(pattern), re.Pattern)
    assert rex.sub(pattern, repl, subject) == re.sub(pattern, repl, subject)
    assert rex.subn(pattern, repl, subject) == re.subn(pattern, repl, subject)

    # Both backends walked the same subject, so the two runs pair up positionally.
    theirs = [m for m in seen if isinstance(m, re.Match)]
    mine = [m for m in seen if not isinstance(m, re.Match)]
    assert mine  # the linear path really ran
    assert len(mine) == len(theirs)
    for attr in read:
        assert hasattr(mine[0], attr), f"irgx match is missing .{attr}"
    for irgx_match, stdlib_match in zip(mine, theirs, strict=True):
        assert irgx_match.span() == stdlib_match.span()
        assert irgx_match.groups() == stdlib_match.groups()
        assert irgx_match.groupdict() == stdlib_match.groupdict()


# ── the safety win: catastrophic backtracking is neutralized on the irgx path ────

# The textbook ReDoS: nested quantifier with a failing tail. On a backtracking
# engine this is ~2^n; on an automaton it is linear and returns immediately.
_CATASTROPHIC = r"(a+)+$"
_EVIL_INPUT = "a" * 40 + "!"


def _run_with_timeout[T](fn: Callable[[], T], timeout: float) -> T | None:
    """Run `fn` on a daemon thread; raise TimeoutError if it outlives `timeout`.

    Daemon so a regressed (backtracking) call can never hang interpreter exit.
    """
    value: list[T] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            value.append(fn())
        except BaseException as exc:  # re-raised on the calling thread below
            failure.append(exc)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        msg = f"call did not finish within {timeout}s"
        raise TimeoutError(msg)
    if failure:
        raise failure[0]
    return value[0] if value else None


def test_stdlib_re_actually_blows_up_on_the_evil_pattern() -> None:
    """Non-oracle anchor: prove stdlib `re` genuinely hangs on `_CATASTROPHIC`.

    Run it in a child process so a real ReDoS can't wedge this test; a clean
    return inside the budget would mean the pattern *isn't* catastrophic and the
    safety tests below would be vacuous.
    """
    code = f"import re; re.search(r{_CATASTROPHIC!r}, {_EVIL_INPUT!r})"
    with pytest.raises(subprocess.TimeoutExpired):
        # Fully controlled argv (interpreter + constant literal) — no shell, no input.
        subprocess.run([sys.executable, "-c", code], timeout=3.0, check=False)  # noqa: S603


def test_rex_is_redos_safe_on_irgx_else_documents_the_degrade() -> None:
    """Test rex is redos safe on irgx else documents the degrade."""
    if rex._HAVE_IRGX:
        # irgx path: the same pattern that wedges stdlib re returns instantly.
        rx = rex.compile(_CATASTROPHIC)
        assert not isinstance(rx, re.Pattern)  # confirms we are ON the irgx path
        assert (
            _run_with_timeout(lambda: rex.search(_CATASTROPHIC, _EVIL_INPUT), 2.0)
            is None
        )
    else:
        # No backend: rex knowingly degrades to stdlib re for this pattern, so the
        # linear-time guarantee is forgone (pinned, per the module docstring). We
        # assert the degrade WITHOUT running the evil input (it would hang).
        assert isinstance(rex.compile(_CATASTROPHIC), re.Pattern)
