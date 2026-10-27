"""Explicit similarity algorithms preserve scores, rankings, and cutoffs across extras."""

from __future__ import annotations

import builtins
from collections import UserDict
import difflib
from functools import partial
import math
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
        monkeypatch.setattr(fuzz, "jaro_winkler", fuzz._jaro_winkler)


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
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio", "jaro_winkler"])
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
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio", "jaro_winkler"])
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
                if fullname == "unstd.text._text_native":
                    raise ModuleNotFoundError("bundled kernel deliberately unavailable")
                return None

        sys.meta_path.insert(0, BlockBackends())
        from unstd.text import fuzz
        from unstd.pack import binpack

        assert fuzz._HAVE_RAPIDFUZZ is False
        assert fuzz._native_distance is None
        assert binpack._HAVE_NUMPY is False
        prefix, suffix = "shared-prefix" * 1000, "shared-suffix" * 1000
        assert fuzz.distance(prefix + "kitten" + suffix, prefix + "sitting" + suffix) == 3
        assert fuzz.distance(prefix + suffix, prefix + "abc" + suffix) == 3
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
        assert "unstd.text._text_native" not in sys.modules
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
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio", "jaro_winkler"])
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
@pytest.mark.parametrize("algorithm", ["indel_ratio", "difflib_ratio", "jaro_winkler"])
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


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
@pytest.mark.parametrize("container", [list, tuple])
def test_jaro_matching_enters_builtin_batch_kernels(
    monkeypatch: pytest.MonkeyPatch, container
) -> None:
    from rapidfuzz import process

    np = pytest.importorskip("numpy")
    calls = []
    for name in ("extractOne", "extract", "cdist"):
        original = getattr(process, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            calls.append((_name, kwargs))
            return _original(*args, **kwargs)

        monkeypatch.setattr(process, name, observed)
    query = "Martha Smith"
    choices = container(["Marhta Smith", "Maria Smith", "Marhta Smith", "Jane Doe"])
    scorer = fuzz.jaro_winkler
    scored = [(choice, scorer(query, choice)) for choice in choices]
    ranked = sorted(scored, key=lambda hit: hit[1], reverse=True)
    assert fuzz.best_match(query, choices, scorer=scorer) == ranked[0]
    assert fuzz.extract(query, choices, scorer=scorer, limit=None) == ranked
    assert fuzz.cdist(container([query]), choices, scorer=scorer, workers=2) == [
        [score for _, score in scored]
    ]
    assert [name for name, _ in calls] == ["extractOne", "extract", "cdist"]
    assert all(kwargs["scorer"] is scorer for _, kwargs in calls)
    assert all("score_cutoff" not in kwargs for _, kwargs in calls)
    assert calls[-1][1]["dtype"] is np.float64
    assert calls[-1][1]["workers"] == 2


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
def test_jaro_custom_weight_partial_retains_python_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rapidfuzz import process

    def forbidden(*args, **kwargs):
        pytest.fail("custom scorer wrappers must retain Python iteration")

    for name in ("extractOne", "extract", "cdist"):
        monkeypatch.setattr(process, name, forbidden)
    scorer = partial(fuzz.jaro_winkler, prefix_weight=0.2)
    choices = ["Marhta", "Martha", "Jane"]
    scores = [scorer("Martha", choice) for choice in choices]
    assert fuzz.best_match("Martha", choices, scorer=scorer) == ("Martha", 1.0)
    assert fuzz.extract("Martha", choices, scorer=scorer, limit=None) == sorted(
        zip(choices, scores, strict=True), key=lambda hit: hit[1], reverse=True
    )
    assert fuzz.cdist(["Martha"], choices, scorer=scorer) == [scores]


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
def test_jaro_perfect_match_still_exhausts_arbitrary_iterables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rapidfuzz import process

    def forbidden(*args, **kwargs):
        pytest.fail("Jaro arbitrary iterables must keep their producer hooks")

    monkeypatch.setattr(process, "extractOne", forbidden)
    visited = []

    def choices():
        for choice in ["Martha", None, "Jane"]:
            visited.append(choice)
            yield choice

    assert fuzz.best_match("Martha", choices(), scorer=fuzz.jaro_winkler) == (
        "Martha",
        1.0,
    )
    assert visited == ["Martha", None, "Jane"]

    def failed_choices():
        yield "Martha"
        msg = "producer failed after perfect match"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="producer failed after perfect match"):
        fuzz.best_match("Martha", failed_choices(), scorer=fuzz.jaro_winkler)


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
@pytest.mark.parametrize("shape", ["query", "choice", "list", "tuple", "none", "nan"])
def test_jaro_unfamiliar_values_keep_scalar_behavior(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    from rapidfuzz import process

    class Text(str):
        pass

    class Choices(list):
        pass

    class TupleChoices(tuple):
        pass

    def forbidden(*args, **kwargs):
        pytest.fail("unfamiliar input shapes must retain scalar scoring")

    for name in ("extractOne", "extract", "cdist"):
        monkeypatch.setattr(process, name, forbidden)
    query = Text("Martha") if shape == "query" else "Martha"
    choices = ["Martha", Text("Marhta") if shape == "choice" else "Marhta"]
    if shape == "list":
        choices = Choices(choices)
    elif shape == "tuple":
        choices = TupleChoices(choices)
    elif shape == "none":
        choices.append(None)
    elif shape == "nan":
        choices.append(math.nan)
    scorer = fuzz.jaro_winkler
    scored = [(choice, scorer(query, choice)) for choice in choices]
    assert fuzz.best_match(query, choices, scorer=scorer) == ("Martha", 1.0)
    assert fuzz.extract(query, choices, scorer=scorer, limit=None) == sorted(
        scored, key=lambda hit: hit[1], reverse=True
    )
    assert fuzz.cdist([query], choices, scorer=scorer) == [
        [score for _, score in scored]
    ]


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
def test_jaro_matrix_without_numpy_uses_scalar_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def without_numpy(name: str, *args: object, **kwargs: object) -> object:
        if name == "numpy":
            msg = "numpy blocked for Jaro batch test"
            raise ImportError(msg)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_numpy)
    choices = ["Martha", "Marhta", "Jane"]
    assert fuzz.cdist(["Martha"], choices, scorer=fuzz.jaro_winkler) == [
        [fuzz.jaro_winkler("Martha", choice) for choice in choices]
    ]


@pytest.mark.usefixtures("scoring_backend")
@pytest.mark.parametrize("limit", [None, 0, 1, 2, 3, 4])
def test_jaro_matching_preserves_ties_limits_and_adjacent_cutoffs(limit: int) -> None:
    query, choices = "Martha", ["Marhta", "Jane", "Marhta"]
    scorer = fuzz.jaro_winkler
    score = scorer(query, choices[0])
    for cutoff in (score, math.nextafter(score, math.inf)):
        ranked = sorted(
            ((c, s) for c in choices if (s := scorer(query, c)) >= cutoff),
            key=lambda hit: hit[1],
            reverse=True,
        )
        assert fuzz.best_match(query, choices, scorer=scorer, score_cutoff=cutoff) == (
            ranked[0] if ranked else None
        )
        assert (
            fuzz.extract(
                query, choices, scorer=scorer, score_cutoff=cutoff, limit=limit
            )
            == ranked[:limit]
        )
        matrix = [[s if (s := scorer(query, c)) >= cutoff else 0.0 for c in choices]]
        assert (
            fuzz.cdist([query], choices, scorer=scorer, score_cutoff=cutoff) == matrix
        )


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
@pytest.mark.parametrize("cutoff_kind", ["subclass", "foreign"])
@pytest.mark.parametrize("operation", ["best_match", "extract", "cdist"])
def test_jaro_stateful_cutoff_retains_scalar_comparison_order(
    monkeypatch: pytest.MonkeyPatch, cutoff_kind: str, operation: str
) -> None:
    from rapidfuzz import process

    class Stateful:
        def __init__(self):
            self.scores = []

        def __le__(self, score):
            self.scores.append(score)
            return len(self.scores) > 1

    class FloatCutoff(float, Stateful):
        def __new__(cls):
            return float.__new__(cls, 0.0)

        def __le__(self, score):
            return Stateful.__le__(self, score)

    cutoff_type = FloatCutoff if cutoff_kind == "subclass" else Stateful
    cutoff, expected_cutoff = cutoff_type(), cutoff_type()
    queries, choices = ["Martha", "Jane"], ["Martha", "Marhta", "Jane"]
    scorer = fuzz.jaro_winkler

    def forbidden(*args, **kwargs):
        pytest.fail("custom cutoff comparisons must retain Python iteration")

    for name in ("extractOne", "extract", "cdist"):
        monkeypatch.setattr(process, name, forbidden)
    if operation == "cdist":
        expected = [
            [s if (s := scorer(q, c)) >= expected_cutoff else 0.0 for c in choices]
            for q in queries
        ]
        actual = fuzz.cdist(queries, choices, scorer=scorer, score_cutoff=cutoff)
    else:
        hits = [
            (c, s) for c in choices if (s := scorer(queries[0], c)) >= expected_cutoff
        ]
        expected = (
            max(hits, key=lambda hit: hit[1], default=None)
            if operation == "best_match"
            else sorted(hits, key=lambda hit: hit[1], reverse=True)
        )
        actual = getattr(fuzz, operation)(
            queries[0], choices, scorer=scorer, score_cutoff=cutoff
        )
    assert actual == expected
    assert cutoff.scores == expected_cutoff.scores
    assert len(cutoff.scores) == len(choices) * (2 if operation == "cdist" else 1)


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="RapidFuzz batch routing")
def test_jaro_custom_limit_is_indexed_after_scoring_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rapidfuzz import process

    choices = ["Marhta", "Jane"]
    expected = [("Marhta", fuzz.jaro_winkler("Martha", "Marhta"))]

    class Limit:
        def __lt__(self, other):
            return False

        def __index__(self):
            choices.clear()
            return 1

    def forbidden(*args, **kwargs):
        pytest.fail("custom slice indices must run after scalar scoring")

    monkeypatch.setattr(process, "extract", forbidden)
    assert fuzz.extract("Martha", choices, scorer=fuzz.jaro_winkler, limit=Limit()) == (
        expected
    )
    assert choices == []
