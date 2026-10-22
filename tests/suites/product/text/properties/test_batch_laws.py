"""Generated ranking, tie, cutoff, limit, and all-pairs laws for the batch helpers.

The oracle is the documented selection rule applied to the helper's own scalar
scorer: keep ``score >= cutoff``, order by descending score with ties in input
order, then truncate to ``limit``. ``cdist`` must equal that scorer per cell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.environment.backends import require_backend
from unstd.text import fuzz


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture(scope="module", params=["portable", "rapidfuzz"])
def backend(request: pytest.FixtureRequest) -> Iterator[str]:
    patch = pytest.MonkeyPatch()
    if request.param == "rapidfuzz":
        require_backend("rapidfuzz")
    else:
        patch.setattr(fuzz, "_HAVE_RAPIDFUZZ", False)
        patch.setattr(fuzz, "ratio", fuzz._difflib_ratio)
        patch.setattr(fuzz, "indel_ratio", fuzz._indel_ratio)
    yield request.param
    patch.undo()


scorer_names = pytest.mark.parametrize(
    "scorer_name", ["indel_ratio", "difflib_ratio", None]
)


def _scorers(
    name: str | None,
) -> tuple[Callable[[str, str], float] | None, Callable[[str, str], float]]:
    """The ``scorer=`` argument and the scalar function it selects."""
    return (None, fuzz.ratio) if name is None else (getattr(fuzz, name),) * 2


words = st.text("abcé😀", max_size=6)
choice_lists = st.lists(
    st.one_of(words, st.sampled_from(["abc", "abc", "cab"])), max_size=9
)


@scorer_names
@given(query=words, choices=choice_lists, data=st.data())
def test_extract_and_best_match_follow_the_selection_rule(
    backend: str,
    scorer_name: str | None,
    query: str,
    choices: list[str],
    data: st.DataObject,
) -> None:
    del backend
    scorer, score = _scorers(scorer_name)
    scored = [(c, score(query, c)) for c in choices]
    cutoff = data.draw(st.sampled_from([0.0, *(s for _, s in scored)]))
    limit = data.draw(st.sampled_from([None, 0, 1, len(choices), len(choices) + 1]))
    ranked = sorted(
        (h for h in scored if h[1] >= cutoff), key=lambda h: h[1], reverse=True
    )
    assert (
        fuzz.extract(
            query, iter(choices), scorer=scorer, score_cutoff=cutoff, limit=limit
        )
        == ranked[:limit]
    )
    assert (
        fuzz.extract(query, choices, scorer=scorer, score_cutoff=cutoff, limit=limit)
        == ranked[:limit]
    )
    best = ranked[0] if ranked else None
    assert (
        fuzz.best_match(query, iter(choices), scorer=scorer, score_cutoff=cutoff)
        == best
    )
    assert fuzz.best_match(query, choices, scorer=scorer, score_cutoff=cutoff) == best


@scorer_names
@given(
    queries=st.lists(words, max_size=5),
    choices=st.lists(words, max_size=5),
    cutoff=st.sampled_from([0.0, 0.5, 2 / 3, 0.8, 1.0]),
)
def test_cdist_equals_its_scalar_path_cell_by_cell(
    backend: str,
    scorer_name: str | None,
    queries: list[str],
    choices: list[str],
    cutoff: float,
) -> None:
    del backend
    scorer, score = _scorers(scorer_name)
    matrix = fuzz.cdist(queries, choices, scorer=scorer, score_cutoff=cutoff)
    assert type(matrix) is list
    assert all(
        type(row) is list and all(type(x) is float for x in row) for row in matrix
    )
    assert matrix == [
        [s if (s := score(q, c)) >= cutoff else 0.0 for c in choices] for q in queries
    ]
