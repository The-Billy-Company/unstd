"""Bounded native text kernels preserve the portable Unicode scoring contracts."""

from __future__ import annotations

from itertools import product

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.text import fuzz


native = pytest.importorskip("unstd.text._text_native")


def _reference_distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous = row
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(
                min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
    return row[-1]


def test_exhaustive_short_strings_match_independent_distance_matrix() -> None:
    words = ["".join(chars) for n in range(5) for chars in product("ab", repeat=n)]
    for a, b in product(words, repeat=2):
        assert native.distance(a, b) == _reference_distance(a, b)


@given(st.text(max_size=80), st.text(max_size=80))
def test_generated_unicode_distance(a: str, b: str) -> None:
    assert native.distance(a, b) == _reference_distance(a, b)


@pytest.mark.parametrize("width", [1, 2, 31, 63, 64, 65, 127, 128, 129, 511, 1024])
def test_word_carries_and_top_word_mask(width: int) -> None:
    a = ("a😀\ud800b" * (width // 4 + 1))[:width]
    cases = [
        (a, "b" + a),
        (a, a[:-1] + "c"),
        (a, a[1:] + a[:1]),
        (a, "x" * width),
        (a, a * 2),
    ]
    for left, right in cases:
        expected = fuzz._string_distance(left, right)
        assert native.distance(left, right) == expected
        assert native.distance(right, left) == expected


@given(
    st.text(alphabet="abé😀\ud800\udfff", min_size=63, max_size=180),
    st.text(alphabet="abé😀\ud800\udfff", min_size=63, max_size=180),
)
def test_generated_multiword_carries(a: str, b: str) -> None:
    assert native.distance(a, b) == _reference_distance(a, b)


def test_unicode_mask_hash_collisions_and_full_alphabet() -> None:
    # These code points all occupy the same initial native hash slot.
    alphabet = "".join(chr(0x1000 + 512 * i) for i in range(256))
    a = alphabet * 4
    b = a[17:] + a[:17]
    assert native.distance(a, b) == fuzz._string_distance(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("", ""),
        ("", "x"),
        ("a", "a"),
        ("a", "b"),
        ("gecaedbc", "acde"),
        ("CRATE", "TRACE"),
        ("Müller", "Muller"),
        ("\ud800😀a\udfff", "\udfff😀b\ud800"),
        ("a" * 80, "a" * 79 + "b"),
    ],
)
def test_jaro_preserves_portable_float_operations(
    a: str, b: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fuzz, "_native_jaro", None)
    assert native.jaro(a, b) == fuzz._jaro(a, b)


@given(st.text(max_size=80), st.text(max_size=80))
def test_generated_unicode_jaro(a: str, b: str) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(fuzz, "_native_jaro", None)
        expected = fuzz._jaro(a, b)
    assert native.jaro(a, b) == expected


def test_exhaustive_repeated_matches_and_transpositions() -> None:
    words = ["".join(chars) for n in range(5) for chars in product("ab", repeat=n)]
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(fuzz, "_native_jaro", None)
        for a, b in product(words, repeat=2):
            assert native.jaro(a, b) == fuzz._jaro(a, b)


@pytest.mark.parametrize("weight", [-1.0, 0.0, 0.1, 0.25, 1.0])
@pytest.mark.parametrize(
    ("a", "b"), [("Jane", "John"), ("Jon", "Jonathan"), ("Dwayne", "Duane")]
)
def test_winkler_retains_python_prefix_arithmetic(
    weight: float, a: str, b: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fuzz, "_native_jaro", native.jaro)
    actual = fuzz._jaro_winkler(a, b, prefix_weight=weight)
    monkeypatch.setattr(fuzz, "_native_jaro", None)
    assert actual == fuzz._jaro_winkler(a, b, prefix_weight=weight)


@pytest.mark.parametrize("kernel", [native.distance, native.jaro])
def test_foreign_values_defer_without_running_user_code(kernel) -> None:
    class Foreign:
        def __eq__(self, other):
            msg = "native comparison called user code"
            raise AssertionError(msg)

        def __str__(self):
            msg = "native conversion called user code"
            raise AssertionError(msg)

    class Letters(str):
        def __iter__(self):
            msg = "native iteration called user code"
            raise AssertionError(msg)

    assert kernel(Foreign(), "a") is NotImplemented
    assert kernel("a", Foreign()) is NotImplemented
    assert kernel(Letters("a"), "b") is NotImplemented
    assert kernel("b", Letters("a")) is NotImplemented
    assert kernel(["a"], ["b"]) is NotImplemented


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("a" * 65537, "b" * 65538),
        ("a", "b" * 262145),
        ("a" * 8192, "b" * 32769),
        ("".join(chr(i) for i in range(257)), "a" * 258),
    ],
)
def test_distance_resource_bounds_defer(a: str, b: str) -> None:
    assert native.distance(a, b) is NotImplemented


@pytest.mark.parametrize(("a", "b"), [("a" * 65537, "b"), ("a" * 2048, "b" * 2049)])
def test_jaro_resource_bounds_defer(a: str, b: str) -> None:
    assert native.jaro(a, b) is NotImplemented


def test_public_fallback_bodies_survive_missing_or_deferred_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def defer(a, b):
        calls.append((a, b))
        return NotImplemented

    monkeypatch.setattr(fuzz, "_native_distance", defer)
    monkeypatch.setattr(fuzz, "_native_jaro", defer)
    assert fuzz._levenshtein("kitten", "sitting") == 3
    assert fuzz._jaro("CRATE", "TRACE") == 0.7333333333333334
    assert calls == [("kitten", "sitting"), ("CRATE", "TRACE")]
    monkeypatch.setattr(fuzz, "_native_distance", None)
    monkeypatch.setattr(fuzz, "_native_jaro", None)
    assert fuzz._levenshtein("kitten", "sitting") == 3
    assert fuzz._jaro("CRATE", "TRACE") == 0.7333333333333334
