"""Hand-derived tie, cutoff, limit, one-shot, worker, matrix-size, and divergence cases.

Scores are LCS counts worked by hand: against ``abcbc``, ``abccc`` and ``bcabc``
share four characters (Indel 0.8) and ``abcaa`` shares three (0.6).
"""

from __future__ import annotations

import difflib
import math
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from unstd.text import fuzz


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(params=["portable", "rapidfuzz"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "rapidfuzz":
        require_backend("rapidfuzz")
    else:
        monkeypatch.setattr(fuzz, "_HAVE_RAPIDFUZZ", False)
        monkeypatch.setattr(fuzz, "ratio", fuzz._difflib_ratio)
        monkeypatch.setattr(fuzz, "indel_ratio", fuzz._indel_ratio)
    return request.param


QUERY = "abcbc"
CHOICES = ["abccc", "bcabc", "abcaa", "abccc"]
RANKED = [("abccc", 0.8), ("bcabc", 0.8), ("abccc", 0.8), ("abcaa", 0.6)]


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("limit", [None, 0, 1, 2, 3, 4, 5])
def test_indel_ties_keep_input_order_and_duplicates_at_every_limit(
    limit: int | None,
) -> None:
    hits = fuzz.extract(QUERY, CHOICES, scorer=fuzz.indel_ratio, limit=limit)
    assert hits == RANKED[:limit]
    assert fuzz.best_match(QUERY, CHOICES, scorer=fuzz.indel_ratio) == ("abccc", 0.8)


@pytest.mark.usefixtures("backend")
def test_cutoff_keeps_equal_scores_and_drops_the_next_float_below() -> None:
    above = math.nextafter(0.8, 1.0)
    between = math.nextafter(0.6, 1.0)
    kw = {"scorer": fuzz.indel_ratio, "limit": None}
    assert fuzz.extract(QUERY, CHOICES, score_cutoff=0.8, **kw) == RANKED[:3]
    assert fuzz.extract(QUERY, CHOICES, score_cutoff=between, **kw) == RANKED[:3]
    assert fuzz.extract(QUERY, CHOICES, score_cutoff=0.6, **kw) == RANKED
    assert fuzz.extract(QUERY, CHOICES, score_cutoff=above, **kw) == []
    assert (
        fuzz.best_match(QUERY, CHOICES, scorer=fuzz.indel_ratio, score_cutoff=above)
        is None
    )
    assert fuzz.cdist(
        [QUERY], CHOICES, scorer=fuzz.indel_ratio, score_cutoff=between
    ) == [[0.8, 0.8, 0.0, 0.8]]
    assert fuzz.cdist(
        [QUERY], CHOICES, scorer=fuzz.indel_ratio, score_cutoff=above
    ) == [[0.0] * 4]


def _once(items: list[str]) -> Iterator[str]:
    yield from items


@pytest.mark.usefixtures("backend")
@pytest.mark.parametrize("scorer_name", ["indel_ratio", "difflib_ratio", None])
def test_one_shot_iterators_are_scored_once_like_lists(scorer_name: str | None) -> None:
    scorer = None if scorer_name is None else getattr(fuzz, scorer_name)
    score = fuzz.ratio if scorer is None else scorer
    expected = sorted(
        ((c, score(QUERY, c)) for c in CHOICES), key=lambda h: h[1], reverse=True
    )
    assert fuzz.extract(QUERY, _once(CHOICES), scorer=scorer, limit=None) == expected
    assert fuzz.best_match(QUERY, _once(CHOICES), scorer=scorer) == expected[0]
    assert fuzz.extract(QUERY, _once([]), scorer=scorer) == []
    assert fuzz.best_match(QUERY, _once([]), scorer=scorer) is None


def _corpus(n: int, salt: str) -> list[str]:
    return [f"{salt}{i:03d}{'xyz'[i % 3] * (i % 5)}" for i in range(n)]


@pytest.mark.parametrize("workers", [1, 2, -1])
def test_batch_kernel_workers_do_not_change_the_matrix(workers: int) -> None:
    require_backend("rapidfuzz")
    require_backend("numpy")
    queries, choices = _corpus(40, "q"), _corpus(30, "c")
    for scorer in (None, fuzz.indel_ratio):
        assert fuzz.cdist(queries, choices, workers=workers, scorer=scorer) == [
            [fuzz.indel_ratio(q, c) for c in choices] for q in queries
        ]


@pytest.mark.parametrize("workers", [0, -7, 10**6])
def test_python_matrices_ignore_workers(backend: str, workers: int) -> None:
    queries, choices = _corpus(6, "q"), _corpus(5, "c")
    assert fuzz.cdist(queries, choices, workers=workers, scorer=fuzz.difflib_ratio) == [
        [difflib.SequenceMatcher(None, q, c).ratio() for c in choices] for q in queries
    ]
    if backend == "portable":
        assert fuzz.cdist(queries, choices, workers=workers) == [
            [fuzz.ratio(q, c) for c in choices] for q in queries
        ]


def test_large_all_pairs_matrix_has_query_rows_of_python_floats(backend: str) -> None:
    size = (400, 300) if backend == "rapidfuzz" else (40, 30)
    queries, choices = _corpus(size[0], "q"), _corpus(size[1], "c")
    matrix = fuzz.cdist(queries, choices, scorer=fuzz.indel_ratio, score_cutoff=0.5)
    assert len(matrix) == size[0]
    assert {len(row) for row in matrix} == {size[1]}
    assert all(type(x) is float for row in matrix for x in row)
    assert matrix == [
        [s if (s := fuzz.indel_ratio(q, c)) >= 0.5 else 0.0 for c in choices]
        for q in queries
    ]


def test_autojunk_divergence_between_difflib_and_indel(backend: str) -> None:
    # In b, every "a" is popular (200 > 201 // 100 + 1), so autojunk ignores
    # them and no non-junk character matches; the LCS is still 200 of 402.
    a, b = "x" + "a" * 200, "a" * 200 + "y"
    assert difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() == 400 / 402
    assert fuzz.difflib_ratio(a, b) == 0.0
    assert fuzz.indel_ratio(a, b) == 1.0 - 2 / 402
    assert fuzz.ratio(a, b) == (0.0 if backend == "portable" else 1.0 - 2 / 402)
