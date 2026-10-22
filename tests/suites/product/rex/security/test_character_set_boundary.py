"""Native set operators must not weaken Python character deny lists."""

from __future__ import annotations

import re
import warnings

import pytest

from unstd import rex
from unstd.rex._faithful import _faithful_classes, native_profile


@pytest.mark.parametrize(
    ("pattern", "rejected", "allowed"),
    [
        (r"[^a-z&&b-d]+", "a", "!"),
        (r"[^a-z--b-d]+", "b", "!"),
        (r"[^a-z~~b-d]+", "b", "!"),
        (r"[[abc]]", "a", "a]"),
        (r"[a[bc]]", "a", "a]"),
        (r"[^[abc]]", "d", "d]"),
        (r"(?x)(?-x:#[a[bc]])", "#a", "#a]"),
    ],
)
@pytest.mark.parametrize("binary", [False, True])
def test_native_class_extensions_cannot_turn_denied_text_into_matches(
    pattern, rejected, allowed, binary
):
    if binary:
        pattern, rejected, allowed = (
            pattern.encode(),
            rejected.encode(),
            allowed.encode(),
        )
    replacement = b"-" if binary else "-"
    text = allowed + rejected + allowed
    rex.purge()
    # Application warning filters cannot change regex execution semantics.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        actual, expected = rex.compile(pattern), re.compile(pattern)
    assert expected.fullmatch(rejected) is None
    assert actual.fullmatch(rejected) is None
    assert expected.fullmatch(allowed).group() == allowed
    assert actual.fullmatch(allowed).group() == allowed
    for operation in ("search", "match", "fullmatch"):
        assert getattr(actual, operation)(rejected) is None
    assert [match.span() for match in actual.finditer(text)] == [
        match.span() for match in expected.finditer(text)
    ]
    assert actual.findall(text) == expected.findall(text)
    assert actual.split(text) == expected.split(text)
    assert actual.subn(replacement, text) == expected.subn(replacement, text)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (r"[a\[bc]+", "abc["),
        (r"[]a\[]+", "[]a"),
        ("(?x:[a-z]+ # [[ ignored ]]\n)", "abc"),
        (r"(?# [[ ignored)[a-z]+", "abc"),
    ],
)
def test_literal_brackets_and_commented_brackets_keep_native_matching(pattern, text):
    actual, expected = rex.compile(pattern), re.compile(pattern)
    match = actual.fullmatch(text)
    assert match is not None
    assert not isinstance(match, re.Match)
    assert match.group() == expected.fullmatch(text).group()


@pytest.mark.parametrize(
    ("pattern", "flags", "eligible", "text"),
    [
        ("(?x)# comment " + "\\\n" + " )\na", 0, True, "a"),
        ("(?x:# comment " + "\\\n" + " [[\na)", 0, True, "a"),
        ("# comment " + "\\\n" + " [[\na", re.VERBOSE, True, "a"),
        ("(?# comment " + r"\)" + " [[)a", 0, True, "a"),
        ("(?x)# comment ) [[" + "\\\\\n" + "a", 0, True, "a"),
        ("(?x)# comment " + "\\\\\n" + "[a[bc]]", 0, False, "a]"),
    ],
)
@pytest.mark.parametrize("binary", [False, True])
def test_comment_escapes_preserve_physical_newlines_and_real_class_boundaries(
    pattern, flags, eligible, text, binary
):
    if binary:
        pattern, text = pattern.encode(), text.encode()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        expected = re.compile(pattern, flags)
    assert expected.fullmatch(text) is not None
    assert _faithful_classes(pattern, expected.flags, faithful=False) is eligible
    assert native_profile(pattern, flags) is (False if eligible else None)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
@pytest.mark.parametrize(
    "pattern", [r"[^a-z\&\&b-d]+", r"[^a-z\-\-b-d]+", r"[^a-z\~\~b-d]+"]
)
def test_escaped_class_operators_keep_native_literal_matching(pattern):
    actual, expected = rex.compile(pattern), re.compile(pattern)
    text = "a1&b-2~c"
    match = actual.search(text)
    assert match is not None
    assert not isinstance(match, re.Match)
    assert actual.findall(text) == expected.findall(text)


def test_set_operator_diagnostic_is_emitted_once_like_python():
    observed = []
    for compile_pattern in (rex.compile, re.compile):
        rex.purge()
        with warnings.catch_warnings(record=True) as diagnostics:
            warnings.simplefilter("always", FutureWarning)
            compile_pattern(r"[^a-z&&b-d]+")
        observed.append([(item.category, str(item.message)) for item in diagnostics])
    assert observed[0] == observed[1]
    assert len(observed[0]) == 1


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
@pytest.mark.parametrize(
    ("pattern", "text"),
    [(r"[[:alpha:]]+", "abc"), (r"[[.a.]]", "a"), (r"[[=a=]]", "a")],
)
def test_documented_posix_classes_keep_native_meaning(pattern, text):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        actual = rex.compile(pattern)
    match = actual.fullmatch(text)
    assert match is not None
    assert not isinstance(match, re.Match)
    assert match.group() == text
