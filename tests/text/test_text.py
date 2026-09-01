"""Adversarial + faithfulness tests for ``unstd.text.fuzz``.

These pin the contract callers migrating off ``difflib`` depend on: ``ratio`` is
difflib-scale (0..1), it is *exactly* difflib on the stdlib fallback path (the
path that runs in this dev env), the rapidfuzz Indel fast path stays ``>=``
difflib with its one class of divergence pinned (gestalt < LCS), ``distance`` is
uniform Levenshtein identical on both paths, ``jaro_winkler`` is the kernel's
score on both paths *including* the 0.7 boost threshold, and
``best_match``/``extract`` rank by score with a working ``score_cutoff``.

The rapidfuzz-path assertions are guarded behind availability; the fallback-path
assertions run here and are exact (never `approx`-weakened).
"""

from __future__ import annotations

import difflib

import pytest

from unstd.text import fuzz
from unstd.text.fuzz import _HAVE_RAPIDFUZZ


def _dl(a: str, b: str) -> float:
    """Return the exact stdlib reference this module is a drop-in for."""
    return difflib.SequenceMatcher(None, a, b).ratio()


@pytest.fixture
def force_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the pure-stdlib branch regardless of whether the ``text`` extra is installed — so the fallback path is exercised deterministically even when rapidfuzz is present in the dev venv."""
    monkeypatch.setattr(fuzz, "_HAVE_RAPIDFUZZ", False)


# A spread that provably agrees between Ratcliff-Obershelp (difflib) and Indel
# (rapidfuzz): identical, disjoint, empty, prefix/substring, and the two
# canonical single-edit pairs. gestalt == LCS for each, so both paths return the
# same value — the "overwhelming majority" the README claims.
AGREEING = [
    ("", ""),
    ("a", ""),
    ("abc", "abc"),
    ("abc", "xyz"),
    ("test", "testing"),
    ("kitten", "sitting"),
    ("apple", "aple"),
    ("hello", "hallo"),
]

# The pinned divergence: gestalt (difflib) counts 3 matched chars (the "abc"
# block), but the LCS is "bcbc" (4). So difflib = 2·3/10 = 0.6 while the Indel
# fast path = 2·4/10 = 0.8. Verified against the stdlib in the test below.
DIVERGENCE = ("abcbc", "bcabc")


# ── ratio: scale + endpoints ───────────────────────────────────────────────────


def test_ratio_endpoints() -> None:
    """Test ratio endpoints."""
    assert fuzz.ratio("identical", "identical") == 1.0
    assert fuzz.ratio("abc", "xyz") == 0.0
    assert fuzz.ratio("", "") == 1.0  # both-empty is 1.0 on both backends
    assert 0.0 <= fuzz.ratio("kitten", "sitting") <= 1.0  # difflib scale, not 0..100


# ── ratio: the universal invariant (holds on BOTH paths) ───────────────────────


@pytest.mark.parametrize(("a", "b"), [*AGREEING, DIVERGENCE, ("Sunday", "Saturday")])
def test_ratio_is_never_below_difflib(a: str, b: str) -> None:
    # difflib's gestalt matched-char count M is <= LCS, so the Indel fast path is
    # always >= difflib, and the fallback path *is* difflib. True on either path.
    """Test ratio is never below difflib."""
    assert fuzz.ratio(a, b) >= _dl(a, b) - 1e-9
    assert fuzz.ratio(a, b) <= 1.0


@pytest.mark.parametrize(("a", "b"), AGREEING)
def test_ratio_equals_difflib_on_agreeing_inputs(a: str, b: str) -> None:
    # These pairs agree on both backends (gestalt == LCS), so equality proves the
    # drop-in claim on whichever path is active.
    """Test ratio equals difflib on agreeing inputs."""
    assert fuzz.ratio(a, b) == pytest.approx(_dl(a, b))


# ── ratio: forced-fallback path is *exactly* difflib (always exercised) ────────


@pytest.mark.usefixtures("force_fallback")
@pytest.mark.parametrize(("a", "b"), [*AGREEING, DIVERGENCE, ("Sunday", "Saturday")])
def test_ratio_fallback_is_exactly_difflib(a: str, b: str) -> None:
    # No tolerance: the fallback calls difflib, so it must be bit-identical —
    # including on the divergence pair, where the fast path would differ.
    """Test ratio fallback is exactly difflib."""
    assert fuzz.ratio(a, b) == _dl(a, b)


def test_divergence_pair_difflib_value_is_pinned() -> None:
    # Pins the exact stdlib reference the divergence is measured against.
    """Test divergence pair difflib value is pinned."""
    assert _dl(*DIVERGENCE) == 0.6


# ── ratio: rapidfuzz Indel fast path — relationship + pinned divergence ────────


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="fast-path-only: rapidfuzz absent")
def test_ratio_fast_path_diverges_above_difflib_on_gestalt_lt_lcs() -> None:
    # Where gestalt < LCS the Indel path scores strictly higher than difflib.
    # This is the documented, deliberate divergence — never shipped silently.
    """Test ratio fast path diverges above difflib on gestalt lt lcs."""
    a, b = DIVERGENCE
    assert fuzz.ratio(a, b) == pytest.approx(0.8)  # 2*LCS/(len+len) = 2*4/10
    assert fuzz.ratio(a, b) > _dl(a, b)  # 0.8 > 0.6


# ── distance: uniform Levenshtein, identical on both paths ─────────────────────


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("kitten", "sitting", 3),  # classic Levenshtein worked example
        ("flaw", "lawn", 2),
        ("same", "same", 0),
        ("", "abc", 3),
        ("abc", "", 3),
        ("", "", 0),
    ],
)
def test_distance_is_levenshtein(a: str, b: str, expected: int) -> None:
    # A single well-defined integer, so the DP fallback and the rapidfuzz kernel
    # return the same value — asserted exactly, no path branching needed.
    """Test distance is levenshtein."""
    assert fuzz.distance(a, b) == expected


def test_distance_is_symmetric() -> None:
    """Test distance is symmetric."""
    assert fuzz.distance("abcxyz", "abzzz") == fuzz.distance("abzzz", "abcxyz")


# ── best_match ─────────────────────────────────────────────────────────────────


def test_best_match_picks_highest_and_scores_it() -> None:
    """Test best match picks highest and scores it."""
    hit = fuzz.best_match("aple", ["grape", "apple", "orange"])
    assert hit is not None
    choice, score = hit
    assert choice == "apple"
    assert score == pytest.approx(_dl("aple", "apple"))


def test_best_match_empty_choices_is_none() -> None:
    """Test best match empty choices is none."""
    assert fuzz.best_match("query", []) is None


def test_best_match_score_cutoff_excludes_weak_matches() -> None:
    # "zzz" is disjoint from every choice (score 0) -> a positive cutoff yields None.
    """Test best match score cutoff excludes weak matches."""
    assert fuzz.best_match("zzz", ["apple", "grape"], score_cutoff=0.5) is None
    # A reachable cutoff still returns the strong match.
    hit = fuzz.best_match("apple", ["apple", "grape"], score_cutoff=0.9)
    assert hit is not None
    assert hit[0] == "apple"


def test_best_match_cutoff_is_inclusive_at_the_boundary() -> None:
    # score exactly at the cutoff is kept (>= semantics, matching rapidfuzz).
    """Test best match cutoff is inclusive at the boundary."""
    score = _dl("aple", "apple")
    hit = fuzz.best_match("aple", ["apple"], score_cutoff=score)
    assert hit is not None
    assert hit[0] == "apple"


# ── extract ────────────────────────────────────────────────────────────────────


def test_extract_ranks_best_first() -> None:
    """Test extract ranks best first."""
    choices = ["apple", "grape", "maple", "zzzzz"]
    results = fuzz.extract("aple", choices)
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)  # monotonically non-increasing
    assert results[0][1] == pytest.approx(max(_dl("aple", c) for c in choices))
    assert "zzzzz" not in {c for c, _ in results} or results[-1][0] == "zzzzz"


def test_extract_limit_caps_result_count() -> None:
    """Test extract limit caps result count."""
    choices = ["apple", "grape", "maple", "ample", "staple"]
    assert len(fuzz.extract("aple", choices, limit=2)) == 2
    assert len(fuzz.extract("aple", choices, limit=None)) == len(choices)


def test_extract_score_cutoff_filters() -> None:
    """Test extract score cutoff filters."""
    choices = ["apple", "grape", "xxxxxx"]
    results = fuzz.extract("aple", choices, score_cutoff=0.5)
    kept = {c for c, _ in results}
    assert "apple" in kept
    assert "xxxxxx" not in kept  # disjoint -> below cutoff, dropped
    assert all(s >= 0.5 for _, s in results)


def test_extract_scores_are_difflib_scale() -> None:
    """Test extract scores are difflib scale."""
    for _, score in fuzz.extract("aple", ["apple", "grape", "maple"]):
        assert 0.0 <= score <= 1.0


# ── forced-fallback: the stdlib branch of every helper (always exercised) ──────


@pytest.mark.usefixtures("force_fallback")
def test_distance_fallback_matches_kernel() -> None:
    # The pure-Python Wagner-Fischer DP returns the same integer as the kernel.
    """Test distance fallback matches kernel."""
    assert fuzz.distance("kitten", "sitting") == 3
    assert fuzz.distance("flaw", "lawn") == 2
    assert fuzz.distance("", "") == 0
    assert fuzz.distance("abc", "") == 3


# ── jaro_winkler: the name comparator ─────────────────────────────────────────


#: Name pairs that carry the reason this function exists beside ``ratio``: a
#: shortening is one person, a shared initial is not, and the boost threshold is
#: the line between them. Values are the kernel's, verified below on both paths.
NAMES = [
    ("Jon", "Jonathan", 0.8542),  # written out — a shortening, boosted
    ("Rob", "Robert", 0.8833),
    ("Kate", "Katherine", 0.8704),
    ("Jane", "John", 0.6667),  # a shared initial only — under the threshold
    ("Chen", "Chan", 0.8667),  # two surnames, one vowel apart — still scores high
    ("CRATE", "TRACE", 0.7333),  # over the threshold, but no common prefix
]


@pytest.mark.parametrize(("a", "b", "score"), NAMES)
def test_jaro_winkler_scores_names(a: str, b: str, score: float) -> None:
    """The scale and the threshold, on whichever path this env has."""
    assert fuzz.jaro_winkler(a, b) == pytest.approx(score, abs=5e-5)


def test_jaro_winkler_endpoints() -> None:
    """Test jaro_winkler endpoints."""
    assert fuzz.jaro_winkler("identical", "identical") == 1.0
    assert fuzz.jaro_winkler("abc", "xyz") == 0.0
    assert fuzz.jaro_winkler("", "") == 1.0
    assert fuzz.jaro_winkler("abc", "") == 0.0


def test_jaro_winkler_withholds_the_bonus_below_the_boost_threshold() -> None:
    """The pinned faithfulness fact: Winkler's 0.7 gate is implemented.

    Without it a single shared initial lifts two unrelated names to 0.70, which
    is exactly where a caller's identity threshold tends to sit — so dropping the
    gate would silently start matching strangers.
    """
    assert fuzz.jaro_winkler("Jane", "John") == pytest.approx(0.6667, abs=5e-5)
    assert fuzz.jaro_winkler("Jane", "John", prefix_weight=0.25) == pytest.approx(
        fuzz.jaro_winkler("Jane", "John", prefix_weight=0.0), abs=1e-12
    ), "under the threshold the weight cannot matter — no bonus is being awarded"


@pytest.mark.skipif(not _HAVE_RAPIDFUZZ, reason="fast-path-only: rapidfuzz absent")
def test_jaro_winkler_fallback_is_exactly_the_kernel() -> None:
    """Unlike ``ratio``, this one has no licensed divergence: the formula is
    closed-form, so the stdlib branch must reproduce the kernel to float
    precision on every input — including the ones that stress transposition
    counting (an odd number of mismatched positions is a cycle, not a swap).
    """
    from rapidfuzz.distance import Jaro, JaroWinkler

    hard = [
        *((a, b) for a, b, _ in NAMES),
        ("", ""),
        ("a", ""),
        ("abcbc", "bcabc"),
        ("gecaedbc", "acde"),  # 3 mismatched matched-positions: floors to 1 swap
        ("Dwayne", "Duane"),  # Winkler's own worked example
        ("aBcD", "AbCd"),  # every char case-flipped: no match survives
        ("a" * 40, "a" * 39 + "b"),
        ("Müller", "Muller"),
    ]
    for a, b in hard:
        assert fuzz._jaro(a, b) == Jaro.similarity(a, b), (
            f"jaro diverged on {a!r}/{b!r}"
        )
        for weight in (0.0, 0.1, 0.25):
            assert fuzz._jaro_winkler(a, b, weight) == pytest.approx(
                JaroWinkler.similarity(a, b, prefix_weight=weight), abs=1e-15
            ), f"jaro-winkler diverged on {a!r}/{b!r} at {weight}"


@pytest.mark.usefixtures("force_fallback")
def test_jaro_winkler_fallback_scores_names() -> None:
    """The stdlib branch answers the same on the pairs callers actually pass."""
    for a, b, score in NAMES:
        assert fuzz.jaro_winkler(a, b) == pytest.approx(score, abs=5e-5)


@pytest.mark.usefixtures("force_fallback")
def test_best_match_fallback() -> None:
    """Test best match fallback."""
    hit = fuzz.best_match("aple", ["grape", "apple", "orange"])
    assert hit is not None
    assert hit[0] == "apple"
    assert fuzz.best_match("zzz", ["apple", "grape"], score_cutoff=0.5) is None


@pytest.mark.usefixtures("force_fallback")
def test_extract_fallback_ranks_and_filters() -> None:
    """Test extract fallback ranks and filters."""
    results = fuzz.extract("aple", ["apple", "grape", "xxxxxx"], score_cutoff=0.5)
    kept = {c for c, _ in results}
    assert "apple" in kept
    assert "xxxxxx" not in kept
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


# ── cdist: the all-pairs batch matrix ──────────────────────────────────────────


def test_cdist_matches_pairwise_ratio() -> None:
    """Test cdist matches pairwise ratio."""
    queries = ["aple", "bananna"]
    choices = ["apple", "banana", "grape"]
    matrix = fuzz.cdist(queries, choices)
    assert len(matrix) == len(queries)
    for row, q in zip(matrix, queries, strict=True):
        assert len(row) == len(choices)
        for score, c in zip(row, choices, strict=True):
            assert score == pytest.approx(fuzz.ratio(q, c), abs=1e-6)


def test_cdist_score_cutoff_zeroes_weak_pairs() -> None:
    """Test cdist score cutoff zeroes weak pairs."""
    matrix = fuzz.cdist(["apple"], ["apple", "zzzzzzzz"], score_cutoff=0.5)
    assert matrix[0][0] == pytest.approx(1.0)
    assert matrix[0][1] == 0.0


def test_cdist_empty_inputs() -> None:
    """Test cdist empty inputs."""
    assert fuzz.cdist([], ["apple"]) == []
    assert fuzz.cdist(["apple"], []) == [[]]
    assert fuzz.cdist([], []) == []


@pytest.mark.usefixtures("force_fallback")
def test_cdist_fallback_matches_pairwise_ratio() -> None:
    """Test cdist fallback matches pairwise ratio."""
    queries = ["aple", "bananna"]
    choices = ["apple", "banana", "grape"]
    matrix = fuzz.cdist(queries, choices)
    assert matrix == [[fuzz.ratio(q, c) for c in choices] for q in queries]


@pytest.mark.usefixtures("force_fallback")
def test_cdist_fallback_score_cutoff_zeroes_weak_pairs() -> None:
    """Test cdist fallback score cutoff zeroes weak pairs."""
    matrix = fuzz.cdist(["apple"], ["apple", "zzzzzzzz"], score_cutoff=0.5)
    assert matrix == [[1.0, 0.0]]
