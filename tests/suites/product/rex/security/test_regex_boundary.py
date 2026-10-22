"""Native safety is proved by execution; fallback retains explicit re semantics.

Hostile patterns run in hard-bounded processes, so a regression cannot wedge
pytest or a native thread. Positive controls use the identical compiled shape.
"""

from __future__ import annotations

import re

import pytest

from tests.support.environment.backends import require_backend
from tests.support.parsers.cases import parser_case
from unstd import rex


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a|aa)+$", r"(ab|a)+c"])
def test_native_regular_patterns_survive_hostile_subjects_in_killable_workers(pattern):
    require_backend("irgx")
    positive = b"a" * 128 + (b"c" if pattern.endswith("c") else b"")
    good = parser_case("regex", positive, pattern=pattern, attest="native")
    assert good["kind"] == "value"
    assert good["native"] is True
    assert good["matched"] is True
    assert good["native_calls"] == ["search"]
    assert good["fallback_calls"] == []
    # Long enough to explode for these patterns under a backtracking engine.
    bad = parser_case("regex", b"a" * 20000 + b"!", pattern=pattern, attest="native")
    assert bad["kind"] == "value"
    assert bad["native"] is True
    assert bad["matched"] is False
    assert bad["native_calls"] == ["search"]
    assert bad["fallback_calls"] == []


@pytest.mark.parametrize(
    ("pattern", "subject"),
    [
        (r"(a+)\1$", b"aaaa"),
        (r"(?=abc)abc", b"abc"),
        (r"(?<=a)b", b"ab"),
        (r"(a|a?)+b", b"aaaab"),
    ],
)
def test_unsupported_native_features_are_identified_as_fallback_not_safety_evidence(
    pattern, subject
):
    result = parser_case("regex", subject, pattern=pattern)
    assert result["matched"] is (re.search(pattern, subject.decode()) is not None)
    assert result["native"] is False


@pytest.mark.parametrize("pattern", ["[", "(", r"\q", r"(?P<x>a)(?P<x>b)", r"a{5,2}"])
def test_native_syntax_never_bypasses_pythons_invalid_pattern_rejection(pattern):
    with pytest.raises(re.error) as expected:
        re.compile(pattern)
    with pytest.raises(re.error) as actual:
        rex.compile(pattern)
    assert type(actual.value) is type(expected.value)
    assert actual.value.pos == expected.value.pos
    assert rex.fullmatch("[a-z]+", "safe") is not None


def test_callback_exception_keeps_identity_and_is_never_retried_after_effects():
    seen = []
    failure = RuntimeError("replacement failed")

    def replacement(match):
        seen.append(match.group())
        raise failure

    with pytest.raises(RuntimeError) as caught:
        rex.sub("[a-z]+", replacement, "first second")
    assert caught.value is failure
    assert seen == ["first"]
    assert (
        rex.sub("[a-z]+", lambda match: match.group().upper(), "first second")
        == "FIRST SECOND"
    )
