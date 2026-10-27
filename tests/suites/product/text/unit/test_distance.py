"""The portable distance agrees with an independent full edit-cost matrix."""

from __future__ import annotations

from itertools import product

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.text import fuzz


def _reference(a: str, b: str) -> int:
    costs = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        costs[i][0] = i
    for j in range(len(b) + 1):
        costs[0][j] = j
    for i, left in enumerate(a, 1):
        for j, right in enumerate(b, 1):
            costs[i][j] = min(
                costs[i - 1][j] + 1,
                costs[i][j - 1] + 1,
                costs[i - 1][j - 1] + (left != right),
            )
    return costs[-1][-1]


def test_all_short_binary_strings() -> None:
    words = ["".join(chars) for n in range(5) for chars in product("ab", repeat=n)]
    for a, b in product(words, repeat=2):
        assert fuzz._levenshtein(a, b) == _reference(a, b)


@given(st.text(max_size=80), st.text(max_size=80))
def test_unicode_edit_costs(a: str, b: str) -> None:
    assert fuzz._levenshtein(a, b) == _reference(a, b)


@pytest.mark.parametrize("width", [29, 30, 31, 63, 64, 65, 127, 128, 129, 511, 1024])
def test_carries_across_integer_digits(width: int) -> None:
    a = "a" * width
    b = "b" + "a" * (width - 2) + "c"
    assert fuzz._levenshtein(a, b) == 2
    assert fuzz._levenshtein(b, a) == 2
    assert fuzz._levenshtein(a, "a" * (width * 2)) == width


def test_large_unicode_alphabet_uses_bounded_mask_storage() -> None:
    a = "".join(chr(0x1000 + i) for i in range(300))
    b = a[1:] + a[0]
    assert fuzz._levenshtein(a, b) == _reference(a, b) == 2


def test_highly_unequal_string_lengths() -> None:
    text = "b" * 5000 + "a" + "b" * 5000
    assert fuzz._levenshtein("a", text) == len(text) - 1
    assert fuzz._levenshtein(text, "a") == len(text) - 1
    assert fuzz._levenshtein("c", text) == len(text)


def test_foreign_sequences_keep_unhashable_elements() -> None:
    assert fuzz._levenshtein([[1], [2], [3]], [[1], [4]]) == 2


def test_string_subclass_retains_its_iteration_behavior() -> None:
    class Letters(str):
        def __iter__(self):
            return iter("xyz")

    assert fuzz._levenshtein(Letters("abc"), "xyz") == 0


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("", "ab" * 19),
        ("ab" * 17, ""),
        ("ab" * 17, "ba" * 19),
        ("\x00é\ud800😀" * 7, "\udfff\U0010ffff" * 11),
    ],
    ids=["suffix", "prefix", "both", "unicode"],
)
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("ab", "ba"),
        ("aba", "b"),
        ("", "abab"),
        ("x" + "a" * 63 + "y", "z" + "a" * 64 + "w"),
        ("e\u0301", "é"),
        ("\ud800😀", "\udfff🙂"),
    ],
    ids=[
        "transposition",
        "unequal",
        "empty-core",
        "word-carry",
        "combining",
        "surrogates",
    ],
)
def test_shared_ends_preserve_full_matrix_distance_without_native(
    prefix: str,
    suffix: str,
    left: str,
    right: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The oracle evaluates the complete strings; it does not trim the ends or
    # assume that an edit alignment stays inside the deliberately different core.
    a, b = prefix + left + suffix, prefix + right + suffix
    expected = _reference(a, b)
    monkeypatch.setattr(fuzz, "_native_distance", None)
    assert fuzz._levenshtein(a, b) == expected
    assert fuzz._levenshtein(b, a) == expected
    assert fuzz._string_distance(a, b) == expected
    assert fuzz._string_distance(b, a) == expected


@pytest.mark.parametrize(("left", "right"), [("", ""), ("a", ""), ("", "aaa")])
def test_shared_ends_can_consume_the_complete_portable_pattern(
    left: str, right: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = "a" * 129 + left, "a" * 129 + right
    monkeypatch.setattr(fuzz, "_native_distance", None)
    assert fuzz._levenshtein(a, b) == _reference(a, b)
    assert fuzz._string_distance(a, b) == _reference(a, b)


def test_diverse_shared_ends_do_not_change_portable_unicode_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "".join(chr(0x1000 + i) for i in range(300))
    suffix = "\ud800😀\x00" * 19
    a, b = prefix + "abca" + suffix, prefix + "bcab" + suffix
    monkeypatch.setattr(fuzz, "_native_distance", None)
    assert fuzz._levenshtein(a, b) == _reference(a, b)
