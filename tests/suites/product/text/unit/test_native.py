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


@pytest.mark.parametrize("width", [63, 64, 65, 127, 128, 129, 16383, 16384])
def test_distance_maximum_admitted_work_and_unicode_storage(width: int) -> None:
    # 16384 * ceil(16384 / 64) is exactly the native word-step limit.
    # NULs and lone surrogates must be ordinary characters, without encoding.
    a = ("\x00é\ud800😀" * (width // 4 + 1))[:width]
    assert native.distance(a, a) == 0
    assert native.distance(a, "x" * width) == width
    assert native.distance(a, a[:-1] + "x") == 1


def test_distance_word_budget_defers_immediately_above_limit() -> None:
    assert native.distance("a" * 16384, "b" * 16385) is NotImplemented
    assert native.distance("a" * 16385, "b" * 16385) is NotImplemented


@pytest.mark.parametrize("alphabet", ["ab\x00", "abé", "ab\ud800", "ab😀"])
def test_each_unicode_representation_preserves_jaro_scores(
    alphabet: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fuzz, "_native_jaro", None)
    a = alphabet * 23
    b = a[3:] + alphabet[::-1]
    assert native.jaro(a, b) == fuzz._jaro(a, b)


@pytest.mark.parametrize("left", ["\x00é", "\ud800雪", "😀𝄞"])
@pytest.mark.parametrize("right", ["\x00ö", "\udfff水", "🙂𐀀"])
def test_mixed_unicode_storage_widths_preserve_both_kernels(
    left: str, right: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each input has its own CPython storage width. Reading the second using
    # the first one's element type can overread, truncate or change the score.
    a, b = (left + "ab") * 19, (right + "ba") * 17
    assert native.distance(a, b) == _reference_distance(a, b)
    monkeypatch.setattr(fuzz, "_native_jaro", None)
    assert native.jaro(a, b) == fuzz._jaro(a, b)


@pytest.mark.parametrize("kernel", [native.distance, native.jaro])
@pytest.mark.parametrize("arguments", [(), ("a",), ("a", "b", "c")])
def test_native_call_rejects_wrong_arity(kernel, arguments) -> None:
    with pytest.raises(TypeError, match="takes exactly two arguments"):
        kernel(*arguments)


@pytest.mark.parametrize("kernel", [native.distance, native.jaro])
def test_native_call_rejects_keywords(kernel) -> None:
    with pytest.raises(TypeError):
        kernel(a="a", b="b")


def test_jaro_maximum_admitted_pair_budget() -> None:
    assert native.jaro("a" * 2048, "a" * 2048) == 1.0
    assert native.jaro("a" * 2048, "b" * 2048) == 0.0
    assert native.jaro("a" * 2048, "b" * 2049) is NotImplemented


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
def test_winkler_keeps_weight_validation_and_exact_prefix_arithmetic(
    weight: float, a: str, b: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not 0.0 <= weight <= 1.0:
        for kernel in (native.jaro, None):
            monkeypatch.setattr(fuzz, "_native_jaro", kernel)
            with pytest.raises(
                ValueError, match=r"prefix_weight has to be in the range 0\.0 - 1\.0"
            ):
                fuzz._jaro_winkler(a, b, prefix_weight=weight)
        return
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
    ids=["pattern-length", "text-length", "word-work", "alphabet-size"],
)
def test_distance_resource_bounds_defer(a: str, b: str) -> None:
    assert native.distance(a, b) is NotImplemented


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [
        ("a" * 65536, ""),
        ("", "😀" * 262145),
        ("a" * 8192, "\ud800" * 8192),
        ("".join(chr(0x1000 + i) for i in range(300)), "\x00é" * 11),
    ],
    ids=["pattern-length", "text-length", "word-work", "alphabet-size"],
)
def test_shared_ends_admit_small_residuals_beyond_original_native_limits(
    prefix: str, suffix: str
) -> None:
    # These complete strings exceeded one of the old admission budgets. Their
    # edit distances have a one-substitution proof or the length lower bound,
    # so the test does not use another implementation's affix reduction.
    a, b = prefix + "x" + suffix, prefix + "y" + suffix
    assert native.distance(a, b) == 1
    assert native.distance(b, a) == 1
    a, b = prefix + suffix, prefix + "xyz" + suffix
    assert native.distance(a, b) == 3
    assert native.distance(b, a) == 3


@pytest.mark.parametrize("left_core", ["é", "\ud800", "😀"])
def test_shared_ascii_ends_preserve_different_native_storage_widths(
    left_core: str,
) -> None:
    a, b = (
        "prefix" * 23 + left_core + "suffix" * 19,
        "prefix" * 23 + "x" + "suffix" * 19,
    )
    assert native.distance(a, b) == _reference_distance(a, b) == 1
    assert native.distance(b, a) == 1


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("a" * 65537, "b" * 65538),
        ("a", "b" * 262145),
        ("a" * 8192, "b" * 32769),
        ("".join(chr(i) for i in range(257)), "a" * 258),
    ],
    ids=["pattern-length", "text-length", "word-work", "alphabet-size"],
)
def test_shared_ends_keep_native_residual_resource_limits(a: str, b: str) -> None:
    prefix, suffix = "shared\x00😀", "\ud800suffix"
    assert native.distance(prefix + a + suffix, prefix + b + suffix) is NotImplemented


@pytest.mark.parametrize("end", ["prefix", "suffix"])
def test_native_shared_end_scan_has_a_work_limit(end: str) -> None:
    shared = "a" * (4194304 + 1)
    a, b = (
        (shared + "x", shared + "y")
        if end == "prefix"
        else ("x" + shared, "y" + shared)
    )
    assert native.distance(a, b) is NotImplemented


def test_native_shared_end_scan_admits_its_exact_comparison_budget() -> None:
    equal = "a" * 4194304
    assert native.distance(equal, equal) == 0
    # Each end also tests one unequal pair: two half-ends plus those two
    # mismatches consume exactly the combined prefix/suffix comparison budget.
    shared = "a" * ((4194304 - 2) // 2)
    assert native.distance(shared + "x" + shared, shared + "y" + shared) == 1


@pytest.mark.parametrize(
    ("a", "b"),
    [("a" * 65537, "b"), ("a" * 2048, "b" * 2049)],
    ids=["text-length", "pair-work"],
)
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


def test_public_fallback_preserves_str_subclass_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Letters(str):
        def __iter__(self):
            return iter("xy")

    def forbidden(*args):
        msg = "string subclasses must keep Python iteration behavior"
        raise AssertionError(msg)

    monkeypatch.setattr(fuzz, "_native_distance", forbidden)
    monkeypatch.setattr(fuzz, "_native_jaro", forbidden)
    assert fuzz._levenshtein(Letters("ab"), "xy") == 0
    assert fuzz._jaro(Letters("ab"), "xy") == 1.0
