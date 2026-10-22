"""Generated metric laws against independent textbook references, on every scoring path.

References are written here from the published definitions: Wagner-Fischer
Levenshtein, LCS-based Indel, and Jaro (1989) with Winkler's (1990) prefix bonus
above 0.7 for up to four characters. Each law runs on the portable Python bodies
with native kernels disabled, on the bundled native kernels, and on RapidFuzz.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import math
from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend
from unstd.text import fuzz


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@dataclass(frozen=True)
class Path:
    distance: Callable[[str, str], int]
    indel: Callable[[str, str], float]
    jaro_winkler: Callable[..., float]


@pytest.fixture(scope="module", params=["portable", "native", "rapidfuzz"])
def path(request: pytest.FixtureRequest) -> Iterator[Path]:
    patch = pytest.MonkeyPatch()
    if request.param == "rapidfuzz":
        require_backend("rapidfuzz")
        yield Path(fuzz.distance, fuzz.indel_ratio, fuzz.jaro_winkler)
        return
    if request.param == "native":
        native = pytest.importorskip("unstd.text._text_native")
        patch.setattr(fuzz, "_native_distance", native.distance)
        patch.setattr(fuzz, "_native_jaro", native.jaro)
    else:
        patch.setattr(fuzz, "_native_distance", None)
        patch.setattr(fuzz, "_native_jaro", None)
    yield Path(fuzz._levenshtein, fuzz._indel_ratio, fuzz._jaro_winkler)
    patch.undo()


def _levenshtein(a: str, b: str) -> int:
    table = [
        [i + j if not i * j else 0 for j in range(len(b) + 1)]
        for i in range(len(a) + 1)
    ]
    for i, ca in enumerate(a, 1):
        for j, cb in enumerate(b, 1):
            table[i][j] = min(
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
                table[i - 1][j - 1] + (ca != cb),
            )
    return table[-1][-1]


def _lcs(a: str, b: str) -> int:
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, ca in enumerate(a, 1):
        for j, cb in enumerate(b, 1):
            table[i][j] = (
                table[i - 1][j - 1] + 1
                if ca == cb
                else max(table[i - 1][j], table[i][j - 1])
            )
    return table[-1][-1]


def _indel(a: str, b: str) -> float:
    total = len(a) + len(b)
    return 1.0 - (total - 2 * _lcs(a, b)) / total if total else 1.0


def _jaro(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    used = [False] * len(b)
    a_matches = []
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not used[j] and b[j] == ca:
                used[j] = True
                a_matches.append(ca)
                break
    if not (m := len(a_matches)):
        return 0.0
    b_matches = [cb for cb, hit in zip(b, used, strict=True) if hit]
    half_transpositions = (
        sum(x != y for x, y in zip(a_matches, b_matches, strict=True)) // 2
    )
    return (m / len(a) + m / len(b) + (m - half_transpositions) / m) / 3


def _jaro_winkler(a: str, b: str, weight: float) -> float:
    if (sim := _jaro(a, b)) <= 0.7:
        return sim
    prefix = 0
    while prefix < min(4, len(a), len(b)) and a[prefix] == b[prefix]:
        prefix += 1
    # Winkler bounds the weight by 0.25; heavier admitted weights cap at 1.0.
    return min(sim + prefix * weight * (1.0 - sim), 1.0)


# Surrogate-free Unicode with astral code points, combining marks, and repeats.
_marks = st.sampled_from(
    ["e\u0301", "\u0308", "a\u0300\u0316", "😀", "𝄞", "👍🏽", "Å", "ﬁ"]
)
_chars = st.characters(exclude_categories=("Cs",))
_unicode = st.lists(st.one_of(_chars, _marks), max_size=10).map("".join)
_repeated = st.builds(
    lambda unit, times: unit * times,
    st.text("ab😀\u0301", min_size=1, max_size=3),
    st.integers(1, 6),
)
texts = st.one_of(_unicode, _repeated, st.text("abc", max_size=12))


@given(a=texts, b=texts, c=texts)
def test_levenshtein_is_a_metric_matching_the_textbook_table(
    path: Path, a: str, b: str, c: str
) -> None:
    ab, ba = path.distance(a, b), path.distance(b, a)
    assert type(ab) is int
    assert ab == ba == _levenshtein(a, b)
    assert path.distance(a, a) == 0
    assert abs(len(a) - len(b)) <= ab <= max(len(a), len(b))
    assert (ab == 0) == (a == b)
    assert path.distance(a, c) <= ab + path.distance(b, c)


@given(a=texts, b=texts)
def test_indel_is_normalized_lcs(path: Path, a: str, b: str) -> None:
    expected = _indel(a, b)
    assert path.indel(a, b) == expected
    assert path.indel(b, a) == expected
    assert 0.0 <= expected <= 1.0
    assert path.indel(a, a) == 1.0


@given(a=texts, b=texts, weight=st.sampled_from([0.0, 0.1, 0.25, 1.0]))
def test_jaro_winkler_matches_the_published_definition(
    path: Path, a: str, b: str, weight: float
) -> None:
    actual = path.jaro_winkler(a, b, prefix_weight=weight)
    expected = _jaro_winkler(a, b, weight)
    assert 0.0 <= actual <= 1.0
    if weight == 0.0:
        assert actual == expected == _jaro(a, b)
    else:
        assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=2**-52)
    assert path.jaro_winkler(a, a, prefix_weight=weight) == 1.0


def test_every_short_binary_pair_matches_all_references(path: Path) -> None:
    # Window edges, repeated matches and transpositions all occur within this domain.
    words = ["".join(chars) for n in range(6) for chars in product("ab", repeat=n)]
    for a, b in product(words, repeat=2):
        assert path.distance(a, b) == _levenshtein(a, b), (a, b)
        assert path.indel(a, b) == _indel(a, b), (a, b)
        assert path.jaro_winkler(a, b, prefix_weight=0.0) == _jaro(a, b), (a, b)
        assert math.isclose(
            path.jaro_winkler(a, b),
            _jaro_winkler(a, b, 0.1),
            rel_tol=0.0,
            abs_tol=2**-52,
        ), (a, b)


@given(
    unit=st.text("ab😀\u0301", min_size=1, max_size=3),
    n=st.integers(1, 8),
    m=st.integers(1, 8),
)
def test_repeated_strings_have_closed_form_scores(
    path: Path, unit: str, n: int, m: int
) -> None:
    # unit*n is a subsequence of unit*m when n <= m, so LCS and edits are exact.
    a, b = unit * n, unit * m
    gap = abs(n - m) * len(unit)
    assert path.distance(a, b) == gap
    assert path.indel(a, b) == 1.0 - gap / (len(a) + len(b))
