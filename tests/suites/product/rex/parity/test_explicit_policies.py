"""Explicit fidelity keeps Python meaning without changing legacy native syntax."""

from __future__ import annotations

import re
import warnings

import pytest

from unstd import rex
from unstd.rex.profile import native_profile


@pytest.mark.parametrize(
    "pattern",
    [
        r"a$",
        r"(?:a$|b)",
        r"(?m:a$)(?-m:b$)",
        r"[[:alpha:]]+",
        r"[^a-z&&b-d]+",
    ],
)
@pytest.mark.parametrize("binary", [False, True])
def test_faithful_compiler_declines_native_dialect_differences(pattern, binary):
    if binary:
        pattern = pattern.encode()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        actual, expected = rex.compile_faithful(pattern), re.compile(pattern)
    assert isinstance(actual, re.Pattern)
    for text in ("a\n", "ab\n", "abc", "alpha]", "!b!"):
        if binary:
            text = text.encode()
        assert actual.findall(text) == expected.findall(text)
        assert rex.findall(actual, text) == expected.findall(text)


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
@pytest.mark.parametrize("pattern", [r"[$]+", r"\$+", r"(?m)a$", r"a+\Z"])
def test_parsed_fidelity_keeps_escaped_dollars_and_multiline_anchors_native(pattern):
    actual, expected = rex.compile_faithful(pattern), re.compile(pattern)
    assert not isinstance(actual, re.Pattern)
    text = "$$ a\na"
    assert actual.findall(text) == expected.findall(text)


def test_faithful_anchor_profile_tracks_scoped_flag_additions_and_removals():
    assert native_profile(r"(?m:a$)", 0, faithful=True) is False
    assert native_profile(r"(?m:a$)(?-m:b$)", 0, faithful=True) is None
    assert native_profile(r"(?-m:a$)", re.MULTILINE, faithful=True) is None


def test_faithful_compilation_accepts_compiled_patterns_and_keeps_python_flags():
    python = re.compile("a$", re.IGNORECASE)
    assert rex.compile_faithful(python) is python
    legacy = rex.compile("a$", rex.IGNORECASE)
    faithful = rex.compile_faithful(legacy)
    assert faithful.pattern == "a$"
    assert faithful.flags == python.flags
    assert faithful.findall("A\n") == ["A"]
    with pytest.raises(ValueError, match="flags"):
        rex.compile_faithful(legacy, rex.ASCII)


def test_faithful_compiles_and_declines_are_cached_and_purged(monkeypatch):
    rex.purge()
    monkeypatch.setattr(rex, "_MAXCACHE", 4)
    first = rex.compile_faithful("a$")
    assert rex.compile_faithful("a$") is first
    for index in range(8):
        rex.compile_faithful(f"policy{index}")
    assert len(rex._faithful_cache) == 4
    rex.purge()
    assert not rex._faithful_cache


@pytest.mark.parametrize("compiler", [rex.compile_faithful, rex.compile_linear])
@pytest.mark.parametrize("flags", [2.0, 2.3, "2", None, []])
def test_policy_flags_are_not_silently_coerced(compiler, flags):
    re.purge()
    with pytest.raises(TypeError):
        re.compile("a", flags)
    with pytest.raises(TypeError):
        compiler("a", flags)


@pytest.mark.parametrize("compiler", [rex.compile_faithful, rex.compile_linear])
@pytest.mark.parametrize("flags", [0, re.IGNORECASE, re.UNICODE])
def test_invalid_float_flags_cannot_reuse_an_integer_cache_entry(compiler, flags):
    if compiler is rex.compile_linear and not rex._HAVE_IRGX:
        pytest.skip("native optional backend absent")
    rex.purge()
    compiled = compiler("2", flags)
    with pytest.raises(TypeError, match="flags"):
        compiler("2", float(flags))
    with pytest.raises(TypeError, match="flags"):
        compiler(compiled, 0.0)
    assert compiler("2", int(flags)) is compiled


@pytest.mark.parametrize("compiler", [rex.compile_faithful, rex.compile_linear])
def test_valid_bool_integer_and_regexflag_aliases_remain_accepted(compiler):
    if compiler is rex.compile_linear and not rex._HAVE_IRGX:
        pytest.skip("native optional backend absent")
    rex.purge()
    compiled = compiler("2", False)
    assert compiler("2", 0) is compiled
    assert compiler("2", re.NOFLAG) is compiled
    ascii_compiled = compiler("2", re.ASCII)
    assert compiler("2", int(re.ASCII)) is ascii_compiled
    round_tripped = compiler(compiled, False)
    assert round_tripped.pattern == compiled.pattern
    assert round_tripped.flags == compiled.flags


def test_true_flags_keep_their_python_template_meaning():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        actual, expected = rex.compile_faithful("2", True), re.compile("2", True)
    assert actual.flags == expected.flags
    if rex._HAVE_IRGX:
        with pytest.raises(rex.LinearRegexError):
            rex.compile_linear("2", True)
