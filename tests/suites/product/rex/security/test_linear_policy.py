"""Linear policy refuses backtracking and native operations that rescan suffixes."""

from __future__ import annotations

from contextlib import nullcontext
import re
import subprocess
import sys

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd import rex


def test_missing_backend_cannot_silently_select_python(monkeypatch):
    rex.purge()
    monkeypatch.setattr(rex, "_HAVE_IRGX", False)
    with pytest.raises(rex.LinearRegexError, match=r"unstd\[rex\]"):
        rex.compile_linear("abc")
    with pytest.raises(re.error):
        rex.compile_linear("[")
    assert isinstance(rex.compile_faithful("abc"), re.Pattern)
    rex.purge()


@pytest.fixture
def native():
    if not rex._HAVE_IRGX:
        pytest.skip("native optional backend absent")
    rex.purge()
    yield
    rex.purge()


@pytest.mark.usefixtures("native")
@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)\1",
        r"(?=a)a",
        r"a*",
        r"(a?)+b",
        r"a$",
        r"[[:alpha:]]+",
        r"\s+",
        r"(?i)a+",
        "\ud800",
    ],
)
def test_unprovable_patterns_are_refused_at_compile(pattern):
    diagnostic = (
        pytest.warns(FutureWarning) if "[:alpha:]" in pattern else nullcontext()
    )
    with diagnostic, pytest.raises(rex.LinearRegexError):
        rex.compile_linear(pattern)


@pytest.mark.usefixtures("native")
def test_linear_objects_round_trip_with_metadata_and_no_python_matching():
    rx = rex.compile_linear(r"(?P<letter>[ab])+")
    assert rx.pattern == r"(?P<letter>[ab])+"
    assert rx.flags == re.UNICODE
    assert rx.groups == 1
    assert rx.groupindex == {"letter": 1}
    assert rx.requires_ascii is False
    assert rex.compile_linear(rx) is rx
    assert rex.compile(rx) is rx
    assert rex.compile_faithful(rx) is rx
    assert rex.compile_linear(re.compile("ab", re.ASCII)).flags == re.ASCII
    with pytest.raises(ValueError, match="flags"):
        rex.compile_linear(rx, rex.ASCII)

    class MetadataOnly:
        pattern = rx.pattern
        groups = rx.groups

        def __getattr__(self, name):
            pytest.fail(f"stdlib operation reached: {name}")

    rx._python = MetadataOnly()
    assert rx.search("ab").group() == "ab"
    assert rx.match("ab").span() == (0, 2)
    assert rx.fullmatch("ab").group("letter") == "b"
    with pytest.raises(rex.LinearRegexError, match="scanner"):
        rx.scanner("ab")


@pytest.mark.usefixtures("native")
@pytest.mark.parametrize("operation", ["search", "match", "fullmatch"])
@pytest.mark.parametrize("subject", ["é", "a\ud800", b"ascii", bytearray(b"a")])
def test_subject_refusal_precedes_matching_and_callbacks(operation, subject):
    rx = rex.compile_linear(r"\w+")
    assert rx.requires_ascii is True

    class NeverNative:
        def __getattr__(self, name):
            pytest.fail(f"native operation reached before subject refusal: {name}")

    rx._native = NeverNative()
    with pytest.raises(rex.LinearRegexError, match="subject"):
        getattr(rx, operation)(subject)


@pytest.mark.usefixtures("native")
def test_exact_ascii_and_encodable_unicode_cases_remain_available():
    assert rex.compile_linear(r"\w+", rex.ASCII).search("café").group() == "caf"
    assert rex.compile_linear("é+").search("café éé").group() == "é"
    assert rex.compile_linear(rb"\w+").search(b"caf\xe9").group() == b"caf"
    assert rex.compile_linear(r"(a)|(b)").search("b").groups() == (None, "b")


@pytest.mark.usefixtures("native")
def test_native_shape_refusal_never_uses_python_fullmatch():
    rx = rex.compile_linear(r"(a)+?")

    class Native:
        def fullmatch(self, *args):
            message = "the native longest path cannot provide these captures"
            raise rex._irgx.error(message)

    rx._native = Native()
    with pytest.raises(rex.LinearRegexError, match="fullmatch"):
        rx.fullmatch("aa")


@pytest.mark.usefixtures("native")
def test_lazy_capture_refusal_precedes_returning_a_match():
    rx = rex.compile_linear("(a)")

    class Match:
        def groups(self):
            message = "capture refusal"
            raise rex._irgx.error(message)

    class Native:
        def search(self, *args):
            return Match()

    rx._native = Native()
    with pytest.raises(rex.LinearRegexError, match="search"):
        rx.search("aa")


@pytest.mark.usefixtures("native")
@pytest.mark.parametrize("operation", ["findall", "finditer", "split", "sub", "subn"])
@pytest.mark.parametrize("limit", [-1, 0, 1, 2])
def test_iterative_operations_refuse_before_native_work_or_callback(operation, limit):
    rx = rex.compile_linear(r"(a+z|a)")

    class NeverNative:
        def __getattr__(self, name):
            pytest.fail(f"native operation reached before refusal: {name}")

    rx._native = NeverNative()
    if operation in ("sub", "subn"):
        arguments = (lambda _: pytest.fail("callback started"), "aaaa", limit)
    elif operation == "split":
        arguments = ("aaaa", limit)
    else:
        arguments = ("aaaa",)
    with pytest.raises(rex.LinearRegexError, match=operation):
        getattr(rx, operation)(*arguments)
    with pytest.raises(rex.LinearRegexError, match=operation):
        getattr(rex, operation)(rx, *arguments)


@pytest.mark.usefixtures("native")
@pytest.mark.parametrize("position", [True, sys.maxsize + 1, 1.0])
def test_region_bounds_require_supported_exact_integers(position):
    with pytest.raises(rex.LinearRegexError, match="region bound"):
        rex.compile_linear("a+").search("aa", position)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
@given(
    st.sampled_from(["a+", "[ab]+", "(a)|(b)", "(ab)+", "(a+z|a)"]),
    st.text(alphabet="ab !\n", max_size=32),
    st.integers(min_value=-5, max_value=40),
    st.integers(min_value=-5, max_value=40),
    st.booleans(),
)
def test_generated_linear_operations_keep_python_results(
    pattern, text, pos, end, binary
):
    if binary:
        pattern, text = pattern.encode(), text.encode()
    actual, expected = rex.compile_linear(pattern), re.compile(pattern)
    for operation in ("search", "match", "fullmatch"):
        mine = getattr(actual, operation)(text, pos, end)
        theirs = getattr(expected, operation)(text, pos, end)
        assert (None if mine is None else (mine.span(), mine.groups())) == (
            None if theirs is None else (theirs.span(), theirs.groups())
        )


@pytest.mark.usefixtures("native")
@pytest.mark.parametrize("pattern", [r"(a+)+\Z", r"(a|aa)+\Z", r"(ab|a)+c"])
def test_strict_hostile_matching_finishes_in_killable_process(pattern):
    code = (
        "from unstd import rex; "
        f"rx = rex.compile_linear({pattern!r}); "
        "assert rx.search('a' * 20000 + '!') is None; "
        "assert rx.search('a' * 128 + "
        + ("'c'" if pattern.endswith("c") else "''")
        + ") is not None"
    )
    subprocess.run(  # noqa: S603 - constant interpreter and controlled test source
        [sys.executable, "-c", code], timeout=5, check=True, capture_output=True
    )


@pytest.mark.usefixtures("native")
def test_overlapping_suffix_pattern_keeps_single_match_operations_bounded():
    # Repeated matching with this fixed pattern can rescan every suffix, even
    # without backtracking. Strict policy accepts only one matching operation.
    code = (
        "from unstd import rex; "
        "rx = rex.compile_linear('(a+z|a)'); text = 'a' * 20000; "
        "assert rx.search(text).groups() == ('a',); "
        "assert rx.match(text).groups() == ('a',); "
        "assert rx.fullmatch(text) is None"
    )
    subprocess.run(  # noqa: S603 - constant interpreter and controlled test source
        [sys.executable, "-c", code], timeout=5, check=True, capture_output=True
    )
