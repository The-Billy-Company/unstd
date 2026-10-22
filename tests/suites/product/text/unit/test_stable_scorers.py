"""Explicit similarity algorithms preserve scores, rankings, and cutoffs across extras."""

from __future__ import annotations

import builtins
from collections import UserDict
import difflib
import subprocess
import sys
import textwrap
from types import MappingProxyType
from typing import TYPE_CHECKING, get_type_hints

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.text import fuzz


if TYPE_CHECKING:
    from collections.abc import Callable


_HAVE_RAPIDFUZZ = fuzz._HAVE_RAPIDFUZZ


@pytest.fixture(params=[False, True], ids=["portable", "rapidfuzz"])
def scoring_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Select the scorers an actual base or text-extra import binds."""
    if request.param:
        if not _HAVE_RAPIDFUZZ:
            pytest.skip("rapidfuzz (`text` extra) not installed")
    else:
        monkeypatch.setattr(fuzz, "_HAVE_RAPIDFUZZ", False)
        monkeypatch.setattr(fuzz, "ratio", fuzz._difflib_ratio)
        monkeypatch.setattr(fuzz, "indel_ratio", fuzz._indel_ratio)


def _indel_reference(a: str, b: str) -> float:
    """An independent full edit-cost matrix, with substitution costing two."""
    table = [[i + j for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i, ca in enumerate(a, 1):
        for j, cb in enumerate(b, 1):
            table[i][j] = min(
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
                table[i - 1][j - 1] + (0 if ca == cb else 2),
            )
    total = len(a) + len(b)
    return 1.0 - table[-1][-1] / total if total else 1.0


_PAIRS = [
    ("", ""),
    ("", "é"),
    ("é", ""),
    ("same", "same"),
    ("abc", "xyz"),
    ("abcbc", "bcabc"),
    ("kitten", "sitting"),
    ("Müller", "Muller"),
    ("a\x00😀é\ud800", "😀a\ud800e\u0301"),
    ("b" + "a" * 210, "a" * 210 + "b"),
    ("abcd" * 80, "bcda" * 80),
    ("".join(map(chr, range(300))), "".join(map(chr, reversed(range(300))))),
]


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize(("a", "b"), _PAIRS)
def test_named_scorers_keep_the_selected_algorithm(a: str, b: str) -> None:
    expected = _indel_reference(a, b)
    assert fuzz.indel_ratio(a, b) == expected
    assert fuzz.indel_ratio(b, a) == expected
    assert fuzz.difflib_ratio(a, b) == difflib.SequenceMatcher(None, a, b).ratio()


@pytest.mark.usefixtures("scoring_backend")
def test_indel_string_subclasses_use_their_underlying_unicode_value() -> None:
    class Text(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __len__(self) -> int:
            return 1000

        def __iter__(self):
            return iter("different")

        def __str__(self) -> str:
            return "overridden conversion"

    assert fuzz.indel_ratio(Text("abcbc"), Text("bcabc")) == 0.8
    assert fuzz.indel_ratio(Text(""), "a") == 0.0
    assert fuzz.indel_ratio("a", Text("")) == 0.0


@given(a=st.text(max_size=32), b=st.text(max_size=32))
def test_portable_indel_matches_independent_edit_matrix(a: str, b: str) -> None:
    assert fuzz._indel_ratio(a, b) == _indel_reference(a, b)


@pytest.mark.skipif(
    not _HAVE_RAPIDFUZZ, reason="needs RapidFuzz as an independent oracle"
)
@given(
    a=st.text(alphabet="abcé\x00😀\ud800", max_size=60),
    b=st.text(alphabet="abcé\x00😀\ud800", max_size=60),
)
def test_portable_indel_is_exactly_rapidfuzz_including_rounding(a: str, b: str) -> None:
    from rapidfuzz.distance import Indel

    assert fuzz._indel_ratio(a, b) == Indel.normalized_similarity(a, b)


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio"])
def test_explicit_algorithm_controls_rankings_ties_and_cutoffs(algorithm: str) -> None:
    scorer = getattr(fuzz, algorithm)
    choices = ["bcabc", "abccc", "abcaa"]
    indel = algorithm == "indel_ratio"
    expected = (
        [("bcabc", 0.8), ("abccc", 0.8), ("abcaa", 0.6)]
        if indel
        else [("abccc", 0.8), ("bcabc", 0.6), ("abcaa", 0.6)]
    )
    assert fuzz.best_match("abcbc", iter(choices), scorer=scorer) == expected[0]
    assert fuzz.extract("abcbc", iter(choices), scorer=scorer, limit=None) == expected
    assert fuzz.extract("abcbc", iter(choices), scorer=scorer, limit=1) == expected[:1]
    assert fuzz.extract("abcbc", choices, scorer=scorer, limit=0) == []
    assert fuzz.extract("abcbc", choices, scorer=scorer, score_cutoff=0.8) == [
        hit for hit in expected if hit[1] >= 0.8
    ]
    assert fuzz.cdist(["abcbc"], choices, scorer=scorer, score_cutoff=0.8) == [
        [0.8 if indel else 0.0, 0.8, 0.0]
    ]
    assert fuzz.best_match("abcbc", choices, scorer=scorer, score_cutoff=0.9) is None
    assert fuzz.extract("abcbc", choices, scorer=scorer, score_cutoff=0.9) == []


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio"])
@given(
    a=st.text(alphabet="abé😀", max_size=18),
    b=st.text(alphabet="abé😀", max_size=18),
)
def test_explicit_scorer_keeps_exact_cutoff_for_every_helper(
    algorithm: str, a: str, b: str
) -> None:
    scorer = getattr(fuzz, algorithm)
    score = scorer(a, b)
    assert fuzz.best_match(a, [b], scorer=scorer, score_cutoff=score) == (b, score)
    assert fuzz.extract(a, [b], scorer=scorer, score_cutoff=score) == [(b, score)]
    assert fuzz.cdist([a], [b], scorer=scorer, score_cutoff=score) == [[score]]


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio"])
def test_explicit_scorer_has_consistent_empty_and_invalid_limit_behavior(
    algorithm: str,
) -> None:
    scorer = getattr(fuzz, algorithm)
    assert fuzz.best_match("q", [], scorer=scorer) is None
    assert fuzz.extract("q", [], scorer=scorer) == []
    assert fuzz.cdist([], ["q"], scorer=scorer) == []
    assert fuzz.cdist(["q"], [], scorer=scorer) == [[]]
    with pytest.raises(ValueError, match="limit must be nonnegative"):
        fuzz.extract("q", ["q"], scorer=scorer, limit=-1)


@pytest.mark.usefixtures("scoring_backend")
def test_custom_similarity_receives_only_two_strings_and_retains_ties() -> None:
    def casefold_ratio(a: str, b: str, /) -> float:
        return float(a.casefold() == b.casefold())

    assert fuzz.best_match("a", ["A", "a", "b"], scorer=casefold_ratio) == ("A", 1.0)
    assert fuzz.extract(
        "a", ["A", "a", "b"], scorer=casefold_ratio, score_cutoff=0.5
    ) == [("A", 1.0), ("a", 1.0)]
    assert fuzz.cdist(["a", "B"], ["A", "a", "b"], scorer=casefold_ratio) == [
        [1.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def test_explicit_indel_batch_without_numpy_matches_pairwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import: Callable[..., object] = builtins.__import__

    def without_numpy(name: str, *args: object, **kwargs: object) -> object:
        if name == "numpy":
            msg = "numpy blocked for portable batch test"
            raise ImportError(msg)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_numpy)
    assert fuzz.cdist(["abcbc"], ["bcabc"], scorer=fuzz.indel_ratio) == [[0.8]]


def test_public_scorer_annotations_support_runtime_introspection() -> None:
    for name in ("best_match", "extract", "cdist", "difflib_ratio"):
        assert get_type_hints(getattr(fuzz, name))
    assert isinstance(get_type_hints(fuzz.indel_ratio), dict)
    assert get_type_hints(fuzz)["indel_ratio"] is fuzz._Similarity


def test_base_imports_keep_stable_apis_and_runtime_annotations_usable() -> None:
    script = textwrap.dedent(
        """\
        import importlib.abc
        import sys
        from typing import get_type_hints

        class BlockBackends(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition(".")[0] in {"rapidfuzz", "numpy"}:
                    raise ModuleNotFoundError("optional backend deliberately unavailable")
                return None

        sys.meta_path.insert(0, BlockBackends())
        from unstd.text import fuzz
        from unstd.pack import binpack

        assert fuzz._HAVE_RAPIDFUZZ is False
        assert binpack._HAVE_NUMPY is False
        assert fuzz.ratio("abcbc", "bcabc") == 0.6
        assert fuzz.indel_ratio("abcbc", "bcabc") == 0.8
        assert fuzz.difflib_ratio("abcbc", "bcabc") == 0.6
        assert fuzz.best_match("abcbc", ["bcabc", "abccc"], scorer=fuzz.indel_ratio) == ("bcabc", 0.8)
        assert fuzz.extract("abcbc", ["bcabc"], scorer=fuzz.indel_ratio, score_cutoff=0.8) == [("bcabc", 0.8)]
        assert fuzz.cdist(["abcbc"], ["bcabc"], scorer=fuzz.indel_ratio, score_cutoff=0.8) == [[0.8]]
        assert fuzz.best_match("apple", {"apple": "pear"}, scorer=fuzz.indel_ratio) == ("apple", 1.0)
        assert fuzz.cdist(["a"], ["a"], scorer=fuzz.indel_ratio, score_cutoff=float("nan")) == [[0.0]]
        assert binpack.pack_array_portable("u1", [1]) == bytes([1])
        assert binpack.unpack_array_portable("u1", bytes([1])) == [1]
        try:
            binpack.pack_array_portable("u1", [1.9])
        except binpack.error:
            pass
        else:
            raise AssertionError("portable integer coercion changed")

        for name in ("indel_ratio", "difflib_ratio", "best_match", "extract", "cdist"):
            assert get_type_hints(getattr(fuzz, name))
        assert get_type_hints(fuzz)
        for name in ("pack_array", "unpack_array", "pack_array_portable", "unpack_array_portable"):
            assert get_type_hints(getattr(binpack, name))
        assert get_type_hints(binpack.pack_array_portable)["dtype"] is str
        assert get_type_hints(binpack.unpack_array_portable)["dtype"] is str
        assert get_type_hints(binpack.unpack_array)["return"] == list[float]
        assert not any(name.partition(".")[0] in {"numpy", "rapidfuzz"} for name in sys.modules)
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed script and current interpreter
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio"])
@pytest.mark.parametrize("mapping_kind", ["dict", "userdict", "proxy", "subclass"])
def test_explicit_scorers_rank_mapping_keys_in_iteration_order(
    algorithm: str, mapping_kind: str
) -> None:
    class Choices(dict[str, str]):
        def __iter__(self):
            return iter(("applf", "apple", "pear"))

        def items(self):
            pytest.fail("matching must consume the iterable, not mapping values")

    data = {"apple": "pear", "applf": "orange", "pear": "zzzzz"}
    mappings = {
        "dict": data,
        "userdict": UserDict(data),
        "proxy": MappingProxyType(data),
        "subclass": Choices(data),
    }
    choices = mappings[mapping_kind]
    scorer = getattr(fuzz, algorithm)
    assert fuzz.best_match("apple", choices, scorer=scorer) == ("apple", 1.0)
    expected = sorted(
        [(choice, scorer("appla", choice)) for choice in choices],
        key=lambda hit: hit[1],
        reverse=True,
    )
    assert fuzz.best_match("appla", choices, scorer=scorer) == expected[0]
    assert fuzz.extract("appla", choices, scorer=scorer, limit=None) == expected
    assert expected[0][0] == ("applf" if mapping_kind == "subclass" else "apple")


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio"])
@pytest.mark.parametrize("cutoff", [float("nan"), float("inf"), float("-inf")])
def test_explicit_scorers_apply_one_predicate_to_nonfinite_cutoffs(
    algorithm: str, cutoff: float
) -> None:
    scorer = getattr(fuzz, algorithm)
    choices = ["apple", "pear"]
    scored = [(choice, scorer("apple", choice)) for choice in choices]
    accepted = [hit for hit in scored if hit[1] >= cutoff]
    assert fuzz.best_match("apple", choices, scorer=scorer, score_cutoff=cutoff) == (
        accepted[0] if accepted else None
    )
    assert (
        fuzz.extract("apple", choices, scorer=scorer, score_cutoff=cutoff, limit=None)
        == accepted
    )
    assert fuzz.cdist(["apple"], choices, scorer=scorer, score_cutoff=cutoff) == [
        [score if score >= cutoff else 0.0 for _, score in scored]
    ]


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="legacy RapidFuzz behavior")
def test_omitted_scorer_retains_legacy_mapping_value_selection() -> None:
    expected = fuzz.ratio("apple", "pear")
    assert fuzz.best_match("apple", {"apple": "pear"}) == ("pear", expected)


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="legacy RapidFuzz batch behavior")
def test_omitted_scorer_retains_legacy_nan_batch_cutoff() -> None:
    pytest.importorskip("numpy")
    assert fuzz.cdist(["a"], ["a"], score_cutoff=float("nan")) == [[1.0]]
