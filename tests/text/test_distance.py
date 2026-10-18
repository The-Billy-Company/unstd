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
