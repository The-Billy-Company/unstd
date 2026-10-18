"""Python's Unicode, iteration and callback contract survives native upgrades."""

from __future__ import annotations

import re
import sys
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd import rex


_UNICODE = "a²\u0300Ⅰ_ß İıſK\x1c\x1d\x1e\x1f\t\u0085é😀\ud800\udfff"


def _matches(pattern: Any, text: str | bytes, *bounds: int) -> list[Any]:
    return [
        (m.span(), m.group(), m.groups(), m.groupdict(), m.lastindex, m.lastgroup)
        for m in pattern.finditer(text, *bounds)
    ]


@pytest.mark.parametrize(
    "pattern",
    [
        r"\w+",
        r"\W+",
        r"\d+",
        r"\D+",
        r"\s+",
        r"\S+",
        r"[\w\s]+",
        r"[^\w]+",
        r"[^\d]+",
        r"[^\s]+",
        r"\b\w+\b",
        r"\B.\B",
        r"(?a:\w+)\w+",
        r"(?u:\w+)",
        r"(?a:\b)\w+",
        r"(?i)i+",
        r"(?i)[a-z]+",
        r"(?i)[^i]+",
        r"(?i:\N{LATIN CAPITAL LETTER I WITH DOT ABOVE})",
        r"(?i:ß)",
        r"(?i:a(?-i:b))",
        r"(?a:i)",
    ],
)
@pytest.mark.parametrize("flags", [0, re.ASCII, re.IGNORECASE])
def test_unicode_operations_match_the_running_interpreter(
    pattern: str, flags: int
) -> None:
    mine, theirs = rex.compile(pattern, flags), re.compile(pattern, flags)
    assert _matches(mine, _UNICODE) == _matches(theirs, _UNICODE)
    assert mine.findall(_UNICODE) == theirs.findall(_UNICODE)
    assert mine.split(_UNICODE) == theirs.split(_UNICODE)
    assert mine.subn("-", _UNICODE) == theirs.subn("-", _UNICODE)


@pytest.mark.parametrize("category", ["w", "d", "s", "W", "D", "S"])
def test_categories_cover_every_python_codepoint(category: str) -> None:
    # Includes unassigned characters and surrogates: the oracle is the actual
    # running interpreter, never a duplicated or pinned Unicode table.
    text = "".join(map(chr, range(sys.maxunicode + 1)))
    pattern = f"\\{category}+"
    assert rex.findall(pattern, text) == re.findall(pattern, text)


@pytest.mark.parametrize(
    "pattern", [r"|a", r".*?", r"a*?", r"(a*)*", r"(a*)+b", r"(a?)*b"]
)
def test_empty_matches_and_nullable_captures_follow_python(pattern: str) -> None:
    for text in ("", "a", "aa", "aab", "aba", "éa"):
        mine, theirs = rex.compile(pattern), re.compile(pattern)
        assert _matches(mine, text) == _matches(theirs, text)
        assert mine.findall(text) == theirs.findall(text)
        assert mine.subn("-", text) == theirs.subn("-", text)
        assert mine.split(text) == theirs.split(text)


@pytest.mark.parametrize(
    "pattern", [r"(a(b)?)+", r"((a)|(b))+", r"(ab)+", r"(?P<outer>a(?P<inner>b)?)+"]
)
def test_groups_remember_earlier_iterations(pattern: str) -> None:
    mine, theirs = rex.compile(pattern), re.compile(pattern)
    for text in ("aba", "abba", "abab", "aa"):
        assert _matches(mine, text) == _matches(theirs, text)
        assert mine.findall(text) == theirs.findall(text)
        assert mine.subn(r"\g<0>", text) == theirs.subn(r"\g<0>", text)


@pytest.mark.parametrize(
    "pattern", [r".", r"[^a]", r"[é]", r"\W+", r"\S+", r"\D+", r"(?u:\w+)"]
)
def test_ascii_flag_does_not_split_unicode_characters(pattern: str) -> None:
    mine, theirs = rex.compile(pattern, re.ASCII), re.compile(pattern, re.ASCII)
    for text in ("aé😀İıſK", "a\ud800z"):
        assert _matches(mine, text) == _matches(theirs, text)
        assert mine.findall(text) == theirs.findall(text)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
def test_ascii_subjects_retain_native_categories_and_callbacks() -> None:
    rx = rex.compile(r"(?P<word>\w+)-(\d+)")
    native_hit = rx.search("ab-12 cd-34")
    assert native_hit is not None
    assert not isinstance(native_hit, re.Match)
    unicode_hit = rx.search("²-12")
    assert isinstance(unicode_hit, re.Match)
    assert unicode_hit.group("word") == "²"
    seen: list[bool] = []

    def replace(match: Any) -> str:
        seen.append(isinstance(match, re.Match))
        return match.group(2) + match.group(1)

    assert rx.subn(replace, "ab-12 cd-34") == ("12ab 34cd", 2)
    assert seen == [False, False]
    seen.clear()
    assert rx.subn(replace, "²-12") == ("12²", 1)
    assert seen == [True]


@pytest.mark.parametrize("pattern", [r"\\w", r"[\b]", r"\\b", r"\N{SNOWMAN}"])
def test_escaped_categories_are_literals(pattern: str) -> None:
    rx = rex.compile(pattern)
    assert rx.findall("\\w\\b\b☃") == re.findall(pattern, "\\w\\b\b☃")


@pytest.mark.parametrize(
    "pattern", [r"[a-z]+", r"(?a:\w+)", r"\w+", r"^a+", r"(?m)^a+$", r"a+\Z"]
)
def test_region_offsets_keep_context_and_end_bounds(pattern: str) -> None:
    mine, theirs = rex.compile(pattern), re.compile(pattern)
    for text in ("éaaa\naaa!", "aaa\naaa", "aaa", ""):
        for pos, endpos in ((0, 0), (0, 2), (1, 4), (-2, 99), (99, 99), (3, 1)):
            assert _matches(mine, text, pos, endpos) == _matches(
                theirs, text, pos, endpos
            )
            for name in ("search", "match", "fullmatch"):
                a, b = (
                    getattr(mine, name)(text, pos, endpos),
                    getattr(theirs, name)(text, pos, endpos),
                )
                assert (None if a is None else a.span()) == (
                    None if b is None else b.span()
                )


@pytest.mark.parametrize("pattern", [r"a+", r".+", "\ud800", r"\ud800"])
def test_lone_surrogates_recover_before_callback_or_observable_yield(
    pattern: str,
) -> None:
    text = "a\ud800a"
    mine, theirs = rex.compile(pattern), re.compile(pattern)
    assert _matches(mine, text) == _matches(theirs, text)
    assert mine.findall(text) == theirs.findall(text)
    assert mine.subn("\udfff", text) == theirs.subn("\udfff", text)
    assert mine.split(text) == theirs.split(text)
    seen: list[tuple[int, int]] = []

    def replace(match: Any) -> str:
        seen.append(match.span())
        return "\udfff"

    assert mine.subn(replace, text) == theirs.subn(lambda _: "\udfff", text)
    assert seen == [match.span() for match in theirs.finditer(text)]


@pytest.mark.parametrize(
    "failure",
    [TypeError("callback"), UnicodeEncodeError("utf-8", "\ud800", 0, 1, "callback")],
)
def test_callback_exception_is_never_retried(failure: Exception) -> None:
    calls: list[tuple[int, int]] = []

    def replace(match: Any) -> str:
        calls.append(match.span())
        raise failure

    with pytest.raises(type(failure)) as caught:
        rex.sub(r"a+", replace, "aa aa")
    assert caught.value is failure
    assert calls == [(0, 2)]


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
def test_native_callbacks_preserve_lone_surrogate_returns() -> None:
    calls: list[tuple[int, int]] = []

    def replace(match: Any) -> str:
        assert not isinstance(match, re.Match)
        calls.append(match.span())
        return "\udfff"

    assert rex.subn(r"a+", replace, "aa aa") == re.subn(
        r"a+", lambda _: "\udfff", "aa aa"
    )
    assert calls == [(0, 2), (3, 5)]


def test_compiled_absent_groups_and_negative_limits_are_faithful() -> None:
    for pattern, text in ((r"(a)|(b)", "ab"), (rb"(a)|(b)", b"ab")):
        mine, theirs = rex.compile(pattern), re.compile(pattern)
        assert mine.findall(text) == theirs.findall(text)
        assert mine.subn(
            "-" if isinstance(text, str) else b"-", text, -1
        ) == theirs.subn("-" if isinstance(text, str) else b"-", text, -1)
        assert mine.split(text, -1) == theirs.split(text, -1)


@pytest.mark.parametrize("pattern", [r"\p{L}", r"(?U)a+", r"\uZZZZ", r"["])
def test_native_only_or_malformed_syntax_keeps_python_errors(pattern: str) -> None:
    with pytest.raises(re.error) as oracle:
        re.compile(pattern)
    with pytest.raises(re.error) as mine:
        rex.compile(pattern)
    assert (mine.value.msg, mine.value.pos) == (oracle.value.msg, oracle.value.pos)


def test_incompatible_flag_errors_keep_python_type_and_message() -> None:
    with pytest.raises(ValueError, match="incompatible") as oracle:
        re.compile(r"(?a)(?u)a")
    with pytest.raises(ValueError, match="incompatible") as mine:
        rex.compile(r"(?a)(?u)a")
    assert str(mine.value) == str(oracle.value)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
def test_literal_escape_does_not_require_an_ascii_subject() -> None:
    rx = rex.compile(r"\\w+")
    hit = rx.search("é\\ww")
    assert hit is not None
    assert not isinstance(hit, re.Match)
    assert hit.group() == r"\ww"


_GRAMMAR = st.recursive(
    st.sampled_from(["a", "b", ".", "[ab]", "[^ab]", r"(?a:\w)", r"\w"]),
    lambda child: st.one_of(
        st.tuples(child, child).map(lambda pair: "".join(pair)),
        st.tuples(child, child).map(lambda pair: f"(?:{pair[0]}|{pair[1]})"),
        st.tuples(child, st.sampled_from(["+", "?", "*", "+?"])).map(
            lambda pair: f"({pair[0]}){pair[1]}"
        ),
    ),
    max_leaves=4,
)


@given(
    _GRAMMAR,
    st.text(alphabet="abé²\u0300\ud800 \n", max_size=12),
    st.sampled_from([0, re.ASCII, re.IGNORECASE]),
)
def test_generated_regular_grammar_keeps_results(
    pattern: str, text: str, flags: int
) -> None:
    mine, theirs = rex.compile(pattern, flags), re.compile(pattern, flags)
    assert _matches(mine, text) == _matches(theirs, text)
    assert mine.findall(text) == theirs.findall(text)
    assert mine.split(text) == theirs.split(text)
    assert mine.subn("-", text) == theirs.subn("-", text)
    a, b = mine.fullmatch(text), theirs.fullmatch(text)
    assert (None if a is None else (a.span(), a.groups())) == (
        None if b is None else (b.span(), b.groups())
    )


def test_fullmatch_recovers_when_lazy_captures_need_a_different_path() -> None:
    mine, theirs = rex.compile(r"(a)+?"), re.compile(r"(a)+?")
    a, b = mine.fullmatch("aa"), theirs.fullmatch("aa")
    assert a is not None
    assert b is not None
    assert (a.span(), a.groups()) == (b.span(), b.groups())


def test_callable_none_and_large_limits_keep_python_results() -> None:
    for pattern, text in (("a+", "aa aa"), (b"a+", b"aa aa")):
        mine, theirs = rex.compile(pattern), re.compile(pattern)
        assert mine.subn(lambda _: None, text) == theirs.subn(lambda _: None, text)
        replacement = "-" if isinstance(text, str) else b"-"
        for count in (0, 1, 2**32 + 1, sys.maxsize):
            assert mine.subn(replacement, text, count) == theirs.subn(
                replacement, text, count
            )
            assert mine.split(text, count) == theirs.split(text, count)


def test_buffer_subjects_keep_python_projection_and_errors() -> None:
    from array import array

    mine, theirs = rex.compile(rb"(a)|(b)"), re.compile(rb"(a)|(b)")
    for text in (bytearray(b"ab"), memoryview(b"ab"), array("B", b"ab")):
        assert mine.findall(text) == theirs.findall(text)
        assert mine.subn(b"-", text) == theirs.subn(b"-", text)
    # Buffer element counts need not equal their byte length.
    text = array("I", [0x61616161, 0x61616161])
    assert rex.compile(b"a").split(text, 3) == re.compile(b"a").split(text, 3)


def test_overridden_string_methods_do_not_change_match_data() -> None:
    class Subject(str):
        def isascii(self) -> bool:
            return True

        def encode(self, *args: Any, **kwargs: Any) -> bytes:
            return b"forged"

    text = Subject("²")
    assert rex.findall(r"\w+", text) == re.findall(r"\w+", text) == ["²"]


@pytest.mark.parametrize("pattern", [r"\B", r"\b", r"(?:\B|)"])
@pytest.mark.parametrize("flags", [0, re.ASCII])
def test_empty_boundaries_keep_the_interpreter_version(
    pattern: str, flags: int
) -> None:
    mine, theirs = rex.compile(pattern, flags), re.compile(pattern, flags)
    for text in ("", " ", "\n", "a", "é", "\ud800"):
        assert _matches(mine, text) == _matches(theirs, text)
        assert mine.findall(text) == theirs.findall(text)
        assert mine.split(text) == theirs.split(text)
        assert mine.subn("-", text) == theirs.subn("-", text)
        a, b = mine.fullmatch(text), theirs.fullmatch(text)
        assert (None if a is None else a.span()) == (None if b is None else b.span())
