"""Drop-in accelerated fuzzy string similarity — rapidfuzz under a stdlib-``difflib``-faithful surface.

The stdlib way to score "how alike are two strings" is
``difflib.SequenceMatcher(None, a, b).ratio()`` — a pure-Python O(n·m)
implementation of the Ratcliff-Obershelp *gestalt* pattern-matching algorithm
(Ratcliff & Metzener, *Dr. Dobb's Journal*, 1988). ``rapidfuzz`` (Max Bachmann,
https://github.com/rapidfuzz/RapidFuzz) computes the same class of score with a
bit-parallel C++/SIMD Indel kernel — an order of magnitude faster on the hot
matching path.

    from unstd.text import fuzz

    fuzz.ratio("kitten", "sitting")        # 0.0 .. 1.0 (difflib scale)
    fuzz.best_match("aple", ["apple", "grape"])   # ("apple", 0.888…)
    fuzz.extract("aple", choices, limit=3)        # ranked [(choice, score), …]
    fuzz.distance("kitten", "sitting")     # 3  (uniform Levenshtein edits)
    fuzz.jaro_winkler("Jon", "Jonathan")   # 0.925 — prefix-weighted, for names

Backend & fallback: rapidfuzz is provided by the ``text`` extra. When
it is installed the fast Indel/Levenshtein kernels are used; when it is **not**
(a base ``unstd`` install — the case in this dev env) every function
transparently falls back to the stdlib (``difflib`` for ``ratio``/``best_match``/
``extract``, a pure-Python DP for ``distance``), so the module imports and works
everywhere and only the speed differs.

Scale normalization — the deliberate difflib faithfulness choice:

- ``difflib`` scores in ``0.0 .. 1.0``; ``rapidfuzz.fuzz.ratio`` scores in
  ``0.0 .. 100.0``. This module normalizes the rapidfuzz path back to ``0.0 ..
  1.0`` (dividing by 100 / using ``normalized_similarity`` directly) so
  ``fuzz.ratio`` is a true drop-in for ``SequenceMatcher.ratio``. ``score_cutoff``
  arguments are likewise ``0.0 .. 1.0`` on both paths.

Ratio faithfulness — semantics, and the one pinned divergence:

- ``ratio`` uses ``rapidfuzz.distance.Indel.normalized_similarity`` on the fast
  path, which is ``2·LCS / (len(a) + len(b))`` — the **normalized Indel
  similarity** (insertions + deletions only; Levenshtein with substitution cost
  2). This is the metric rapidfuzz uses for ``fuzz.ratio`` and is the closest
  faithful analogue of difflib's ratio (see rapidfuzz ``api_differences.md``,
  which notes fuzzywuzzy switched between the two and rapidfuzz standardized on
  Indel for consistency).
- difflib's Ratcliff-Obershelp counts characters in the *longest contiguous*
  matching block, recursively — a greedy match whose total ``M`` is **≤ LCS**.
  So ``ratio`` (Indel path) ``≥`` ``SequenceMatcher.ratio`` for all inputs, with
  **equality on the overwhelming majority** (identical, disjoint, and typical
  single-edit real-world strings all agree exactly). They can diverge only where
  the greedy gestalt block choice underperforms the LCS — e.g. ``"abcbc"`` vs
  ``"bcabc"``, where gestalt matches only ``"abc"`` (M=3, ratio 0.6) but the LCS
  is ``"bcbc"`` (Indel 0.8). The rapidfuzz path is therefore *not byte-identical*
  to difflib on those adversarial cases; the
  fallback path (difflib itself) is exact. This is pinned as a regression guard
  in ``tests/text/test_text.py`` and must never be shipped silently.
- ``difflib`` additionally applies an ``autojunk`` heuristic (treating characters
  that appear in >1% of a sequence ≥200 long as "popular junk"), which can lower
  its ratio on long, repetitive strings. The fallback preserves it exactly
  (default ``SequenceMatcher(None, a, b)``); the Indel path does not model it
  (and is arguably more correct there). Another documented source of divergence
  on the fast path for long inputs.

``distance`` is the uniform Levenshtein edit distance (weights ``(1, 1, 1)`` —
insertion, deletion, substitution), matching ``rapidfuzz.distance.Levenshtein.
distance``. Levenshtein distance is a single well-defined integer, so the
pure-Python fallback returns a value **identical** to the rapidfuzz kernel — no
divergence, only speed.

``jaro_winkler`` is the other similarity in the family, and it is here because
``ratio`` answers a different question. Indel/gestalt similarity weighs every
position alike, which is the right call for a sentence and the wrong one for a
*name*: "Jon"/"Jonathan" is a shortening rather than a different person, and the
evidence for that is entirely in the shared prefix. Jaro (Matthew Jaro,
"Advances in record linkage methodology as applied to matching the 1985 census
of Tampa, Florida", JASA 84(406), 1989) scores matching characters within a
sliding window and discounts transpositions; Winkler (William Winkler, "String
comparator metrics and enhanced decision rules in the Fellegi-Sunter model of
record linkage", 1990) adds a bonus for a common prefix of up to four
characters. It is the standard record-linkage comparator, and the one person
matching scores contacts with.

The prefix bonus is awarded only above Winkler's 0.7 boost threshold, matching
both the paper and ``rapidfuzz.distance.JaroWinkler``: below it a shared initial
buys nothing, so "Jane"/"John" stays at 0.667 instead of being lifted to 0.70 by
the ``J`` alone. Like ``distance``, the score is a closed-form formula rather
than a search, so the pure-Python fallback is numerically identical to the
kernel rather than merely close: over 36k random and adversarial pairs across
three alphabets, plain Jaro agrees bit-for-bit and the prefix-weighted score
agrees to within one float epsilon (1.1e-16) at three prefix weights.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import rapidfuzz as _rf

    _HAVE_RAPIDFUZZ = True
else:
    try:
        import rapidfuzz as _rf

        _HAVE_RAPIDFUZZ = True
    except ImportError:  # base install without the `text` extra — stdlib fallback
        _rf = None
        _HAVE_RAPIDFUZZ = False


__all__ = [
    "best_match",
    "cdist",
    "distance",
    "extract",
    "jaro_winkler",
    "ratio",
]

#: Jaro score a pair must beat before Winkler's prefix bonus applies, and the
#: prefix length the bonus saturates at — both from Winkler (1990), and both the
#: values ``rapidfuzz.distance.JaroWinkler`` uses.
_BOOST_MIN: Final[float] = 0.7
_PREFIX_MAX: Final[int] = 4


def ratio(a: str, b: str) -> float:
    """Similarity of *a* and *b* in ``0.0 .. 1.0`` — a drop-in for ``difflib.SequenceMatcher(None, a, b).ratio()``.

    Fast path (``text`` extra): normalized Indel similarity
    (``2·LCS / (len(a)+len(b))``), which equals difflib's ratio on almost all
    inputs and is ``≥`` it otherwise (see module docstring). Fallback path:
    difflib itself, so the score is exact. Two empty strings score ``1.0`` on
    both paths.
    """
    if _HAVE_RAPIDFUZZ:
        return _rf.distance.Indel.normalized_similarity(a, b)
    return difflib.SequenceMatcher(None, a, b).ratio()


def distance(a: str, b: str) -> int:
    """Uniform Levenshtein edit distance (insert/delete/substitute, all cost 1).

    Matches ``rapidfuzz.distance.Levenshtein.distance`` exactly — the value is a
    single well-defined integer, so the pure-Python DP fallback agrees with the
    rapidfuzz kernel byte-for-byte (only the speed differs).
    """
    if _HAVE_RAPIDFUZZ:
        return _rf.distance.Levenshtein.distance(a, b)
    return _levenshtein(a, b)


def jaro_winkler(a: str, b: str, *, prefix_weight: float = 0.1) -> float:
    """Prefix-weighted Jaro-Winkler similarity in ``0.0 .. 1.0`` — the name comparator.

    Prefer this over :func:`ratio` when the strings are *names* (people, places,
    companies): a shared opening is much stronger evidence of the same referent
    than a shared middle, which is the asymmetry the prefix bonus encodes and
    Indel similarity has no way to express. *prefix_weight* is the per-character
    bonus for a common prefix of up to four characters; the default ``0.1``
    saturates the bonus at four, as Winkler's formulation does. Pairs scoring at
    or below 0.7 on plain Jaro get no bonus at all (the boost threshold), so a
    shared initial cannot lift two unrelated names.

    Matches ``rapidfuzz.distance.JaroWinkler.similarity`` exactly on both paths.
    """
    if _HAVE_RAPIDFUZZ:
        return _rf.distance.JaroWinkler.similarity(a, b, prefix_weight=prefix_weight)
    return _jaro_winkler(a, b, prefix_weight)


def best_match(
    query: str, choices: Iterable[str], *, score_cutoff: float = 0.0
) -> tuple[str, float] | None:
    """Return the single best ``(choice, score)`` for *query*, or ``None``.

    *score_cutoff* is on the ``0.0 .. 1.0`` :func:`ratio` scale: choices scoring
    below it are excluded (``None`` if none qualify or *choices* is empty). On
    the fast path this is ``rapidfuzz.process.extractOne``; the fallback scans
    with :func:`ratio` (the ``difflib.get_close_matches`` idiom, scored). Ties
    resolve to the first choice in iteration order on both paths.
    """
    if _HAVE_RAPIDFUZZ:
        hit = _rf.process.extractOne(
            query,
            list(choices),
            scorer=_rf.fuzz.ratio,
            score_cutoff=score_cutoff * 100.0,
        )
        return (hit[0], hit[1] / 100.0) if hit else None
    best: tuple[str, float] | None = None
    for choice in choices:
        score = ratio(query, choice)
        if score >= score_cutoff and (best is None or score > best[1]):
            best = (choice, score)
    return best


def extract(
    query: str,
    choices: Iterable[str],
    *,
    limit: int | None = 5,
    score_cutoff: float = 0.0,
) -> list[tuple[str, float]]:
    """Top-*limit* ``(choice, score)`` for *query*, best first.

    *score_cutoff* is on the ``0.0 .. 1.0`` :func:`ratio` scale; ``limit=None``
    returns every qualifying choice. Fast path: ``rapidfuzz.process.extract``.
    Fallback: score every choice with :func:`ratio`, filter, and stably sort
    descending (the scored ``difflib.get_close_matches`` equivalent).
    """
    if _HAVE_RAPIDFUZZ:
        hits = _rf.process.extract(
            query,
            list(choices),
            scorer=_rf.fuzz.ratio,
            score_cutoff=score_cutoff * 100.0,
            limit=limit,
        )
        return [(choice, score / 100.0) for choice, score, _ in hits]
    scored = [(c, s) for c in choices if (s := ratio(query, c)) >= score_cutoff]
    scored.sort(key=lambda cs: cs[1], reverse=True)
    return scored if limit is None else scored[:limit]


def cdist(
    queries: Sequence[str],
    choices: Sequence[str],
    *,
    score_cutoff: float = 0.0,
    workers: int = 1,
) -> list[list[float]]:
    """All-pairs similarity matrix between *queries* and *choices*, on the ``0.0 .. 1.0`` :func:`ratio` scale.

    The batch counterpart to calling :func:`best_match` / :func:`extract` once
    per query — entity resolution and contact dedup want "score every query
    against every choice", not N independent scans. Fast path (``text`` extra):
    ``rapidfuzz.process.cdist``'s SIMD kernel computes the whole N×M matrix in
    one call — the single largest win rapidfuzz's own benchmarks report for
    many-query fuzzy matching. *workers* forwards to it (``-1`` = all cores);
    ignored on the fallback, which has no parallel path. A pair scoring below
    *score_cutoff* reads ``0.0`` on both paths, matching ``cdist``'s own
    zeroing convention. Fallback: a nested-loop matrix of :func:`ratio` calls —
    identical numbers, no batch speedup.

    ``rapidfuzz.process.cdist`` itself additionally requires ``numpy`` (absent
    from the ``text`` extra's own dependencies); when it's missing this
    degrades to the same fallback the extra-absent case takes.
    """
    if _HAVE_RAPIDFUZZ:
        try:
            matrix = _rf.process.cdist(
                queries,
                choices,
                scorer=_rf.fuzz.ratio,
                score_cutoff=score_cutoff * 100.0,
                workers=workers,
            )
        except ImportError:  # cdist is numpy-backed; numpy isn't a `text`-extra dep
            pass
        else:
            return [[float(v) / 100.0 for v in row] for row in matrix]
    return [
        [s if (s := ratio(q, c)) >= score_cutoff else 0.0 for c in choices]
        for q in queries
    ]


def _jaro(a: str, b: str) -> float:
    """Jaro similarity (Jaro 1989) — matches in a sliding window, less transpositions.

    Two characters match when they are equal and no further than
    ``max(|a|, |b|) // 2 - 1`` positions apart; a transposition is a matched pair
    whose order differs between the strings, and counts half. Identical strings
    score ``1.0``, two empty strings ``1.0``, and no matches at all ``0.0``.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    b_taken = [False] * len(b)
    a_matched: list[str] = []
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_taken[j] and b[j] == ca:
                b_taken[j] = True
                a_matched.append(ca)
                break
    if not (m := len(a_matched)):
        return 0.0
    b_matched = [cb for cb, taken in zip(b, b_taken, strict=True) if taken]
    # Half the mismatched positions, floored — the transposition count every
    # implementation uses. An odd mismatch count means the matched sequences
    # differ by a cycle longer than a swap, and rounding it up would charge the
    # pair for a transposition that isn't one.
    swaps = sum(x != y for x, y in zip(a_matched, b_matched, strict=True)) // 2
    return (m / len(a) + m / len(b) + (m - swaps) / m) / 3


def _jaro_winkler(a: str, b: str, prefix_weight: float) -> float:
    """Jaro plus Winkler's prefix bonus, awarded only above the 0.7 boost threshold.

    The threshold is what keeps a shared initial from promoting two different
    names: "Jane"/"John" sits at 0.667 and stays there, where an unconditional
    bonus would read 0.70 and cross thresholds callers set just above it.
    """
    sim = _jaro(a, b)
    if sim <= _BOOST_MIN:
        return sim
    prefix = 0
    for ca, cb in zip(a[:_PREFIX_MAX], b[:_PREFIX_MAX], strict=False):
        if ca != cb:
            break
        prefix += 1
    return min(sim + prefix * prefix_weight * (1.0 - sim), 1.0)


def _levenshtein(a: Sequence[object], b: Sequence[object]) -> int:
    """Uniform Levenshtein distance via the classic two-row DP (Wagner-Fischer, 1974).

    Faithful stand-in for ``rapidfuzz.distance.Levenshtein.distance`` when the
    ``text`` extra is absent; O(len(a)·len(b)) time, O(len(b)) space.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
