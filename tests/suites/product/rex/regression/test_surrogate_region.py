"""Surrogates outside a search region cannot break delayed match projections.

Found by deterministic fuzz v1 seed 173, iteration 79. Native region matching
can succeed on a clean slice, but its match later encodes the entire original
subject during group extraction. The facade must choose Python before exposing
that match or starting a callback, preserving the original subject and bounds.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from unstd import rex


_FINDING = b"\x00\x00\x02\x00alpha beta\xff"


def _snapshot(match: Any) -> Any:
    if match is None:
        return None
    return (
        match.group(),
        match.groups(),
        match.groupdict(),
        match.span(),
        match.regs,
        match.lastindex,
        match.lastgroup,
        match.string,
        match.pos,
        match.endpos,
        match.expand(r"[\g<0>]"),
    )


@pytest.mark.parametrize("codepoint", [0xD800, 0xDCFF, 0xDFFF])
@pytest.mark.parametrize(
    "position", ["after", "before", "unicode-before", "between-left", "between-right"]
)
@pytest.mark.parametrize("pattern", ["a", r"[a-z]+", r"(?P<word>a+)(b)?"])
@pytest.mark.parametrize("flags", [0, re.ASCII])
def test_delayed_match_data_uses_original_subject_with_surrogates_outside_region(
    codepoint, position, pattern, flags
):
    surrogate = chr(codepoint)
    text, start, end = {
        "after": ("aa" + surrogate, 0, 2),
        "before": (surrogate + "aa", 1, 3),
        "unicode-before": ("éaa" + surrogate, 1, 3),
        "between-left": ("aa" + surrogate + "aa", 0, 2),
        "between-right": ("aa" + surrogate + "aa", 3, 5),
    }[position]
    mine, reference = rex.compile(pattern, flags), re.compile(pattern, flags)
    for verb in ("search", "match", "fullmatch"):
        actual = getattr(mine, verb)(text, start, end)
        expected = getattr(reference, verb)(text, start, end)
        assert _snapshot(actual) == _snapshot(expected)
        if actual is not None:
            assert isinstance(actual, re.Match)
            assert actual.string is text
    assert [_snapshot(match) for match in mine.finditer(text, start, end)] == [
        _snapshot(match) for match in reference.finditer(text, start, end)
    ]
    assert mine.findall(text, start, end) == reference.findall(text, start, end)


@pytest.mark.parametrize("pattern", [r"a+", r"(?P<word>a+)(b)?"])
def test_surrogate_subject_callbacks_observe_fully_projectable_matches_once(pattern):
    text = "aa\udcffaa"
    calls = []
    reference_calls = []

    def replace(match):
        calls.append(_snapshot(match))
        return match.expand(r"[\g<0>]")

    def expected_replace(match):
        reference_calls.append(_snapshot(match))
        return match.expand(r"[\g<0>]")

    assert rex.subn(pattern, replace, text) == re.subn(pattern, expected_replace, text)
    assert calls == reference_calls
    assert len(calls) == 2


def test_exact_fuzz_finding_and_small_region_reproducer_replay():
    from tests.tooling.fuzz.targets.rex import fuzz

    fuzz(_FINDING)
    fuzz(b"\x00\x00\xff")
    actual = rex.compile("a").search("a\udcff", 0, 1)
    assert actual is not None
    assert actual.group() == "a"
    assert actual.string == "a\udcff"


@pytest.mark.skipif(not rex._HAVE_IRGX, reason="native optional backend absent")
def test_native_matches_remain_available_for_encodable_unicode_regions():
    actual = rex.compile(r"(?P<word>[a-z]+)").search("éaa😀", 1, 3)
    assert actual is not None
    assert not isinstance(actual, re.Match)
    assert actual.group() == "aa"
    assert actual.groupdict() == {"word": "aa"}
    assert actual.span() == (1, 3)
