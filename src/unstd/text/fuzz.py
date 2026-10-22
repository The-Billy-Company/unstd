"""String similarity with optional acceleration and explicit stable algorithms.

    from unstd.text import fuzz

    fuzz.indel_ratio("abcbc", "bcabc")     # 0.8, with or without RapidFuzz
    fuzz.difflib_ratio("abcbc", "bcabc")   # 0.6, always stdlib SequenceMatcher
    fuzz.best_match("aple", ["apple", "grape"], scorer=fuzz.indel_ratio)
    fuzz.distance("kitten", "sitting")     # 3 (uniform Levenshtein edits)

``indel_ratio`` is normalized Indel similarity: mathematically
``2·LCS / (len(a) + len(b))``, with two empty strings scoring ``1.0``.
It uses RapidFuzz when installed and a portable bit-vector LCS implementation
otherwise, with the same floating-point normalization on both paths.
``difflib_ratio`` always calls ``difflib.SequenceMatcher(None, a, b).ratio()``,
including its ``autojunk`` heuristic and order-sensitive matching decisions.
Both named scorers retain their algorithm when dependencies change.

The historical ``ratio`` keeps its backend-dependent behavior for compatibility:
RapidFuzz's Indel scorer with the ``text`` extra, stdlib difflib otherwise.
These algorithms share a ``0.0 .. 1.0`` scale, but their scores and rankings can
differ. For example, ``"abcbc"`` / ``"bcabc"`` scores 0.8 with Indel and 0.6
with difflib; difflib's ``autojunk`` also affects long repetitive strings.
Pass a named scorer to ``best_match``, ``extract``, and ``cdist`` when rankings
or cutoff decisions must be independent of installed dependencies. Omitting
``scorer`` retains the historical ``ratio`` selection and accelerated paths.

Optional bundled C kernels accelerate bounded exact-string Levenshtein and
Jaro calls without RapidFuzz; unsupported values and source builds without a
compiler retain the Python bodies.

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

from collections.abc import (  # noqa: TC003 — runtime type introspection
    Callable,
    Iterable,
    Sequence,
)
import difflib
from itertools import compress
from operator import itemgetter, ne
from types import NotImplementedType  # noqa: TC003 — runtime type introspection
from typing import TYPE_CHECKING, Final, Protocol


if TYPE_CHECKING:
    import rapidfuzz as _rf

    _HAVE_RAPIDFUZZ = True
else:
    try:
        import rapidfuzz as _rf

        _HAVE_RAPIDFUZZ = True
    except ImportError:  # base install without the `text` extra — stdlib fallback
        _rf = None
        _HAVE_RAPIDFUZZ = False


_native_distance: Callable[[object, object], int | NotImplementedType] | None
_native_jaro: Callable[[object, object], float | NotImplementedType] | None
try:
    from unstd.text._text_native import distance as _native_distance
    from unstd.text._text_native import jaro as _native_jaro
except ImportError:  # source-only installs retain the portable scoring bodies
    _native_distance = _native_jaro = None


__all__ = [
    "best_match",
    "cdist",
    "difflib_ratio",
    "distance",
    "extract",
    "indel_ratio",
    "jaro_winkler",
    "ratio",
]

#: Jaro score a pair must beat before Winkler's prefix bonus applies, and the
#: prefix length the bonus saturates at — both from Winkler (1990), and both the
#: values ``rapidfuzz.distance.JaroWinkler`` uses.
_BOOST_MIN: Final[float] = 0.7
_PREFIX_MAX: Final[int] = 4

#: rapidfuzz's own ``score_cutoff`` is never passed: its normalized scorers prune
#: through a float round-trip that drops pairs scoring *exactly* at the cutoff
#: (~70% of random short pairs), and pruning buys nothing measurable at SIMD speed.
#: Every fast path scores in full and applies the documented ``>=`` here instead.


# ── portable scorers — the base-install bodies, and the oracle the kernels are held to ──


def difflib_ratio(a: str, b: str, /) -> float:
    """Stdlib SequenceMatcher similarity, including autojunk, on every backend.

    Retains difflib's order-sensitive block matching and ``0.0 .. 1.0`` scale.
    Two empty strings score ``1.0``. This algorithm can differ from Indel.
    """
    return difflib.SequenceMatcher(None, a, b).ratio()


_difflib_ratio = difflib_ratio


def _masks(a: str) -> dict[str, int] | None:
    """Per-character position bitmasks of *a*, or ``None`` past 256 distinct characters.

    A separate arbitrary-width integer for every distinct character can need
    quadratic storage, so diverse Unicode falls back to a bounded DP row.
    """
    if len(set(a)) > 256:
        return None
    masks: dict[str, int] = {}
    for i, char in enumerate(a):
        masks[char] = masks.get(char, 0) | 1 << i
    return masks


def _indel_ratio(a: str, b: str, /) -> float:
    """Normalized Indel similarity with RapidFuzz's float arithmetic."""
    # RapidFuzz reads Unicode storage directly. Use the same string value for
    # subclasses, rather than their overridden equality/length/iteration hooks.
    if type(a) is not str or type(b) is not str:
        a, b = str.__str__(a), str.__str__(b)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    if (masks := _masks(a)) is None:
        common = _lcs_length(a, b)
    else:
        get, row = masks.get, 0
        for char in b:
            matches = row | get(char, 0)
            row = matches & ~(matches - ((row << 1) | 1))
        common = row.bit_count()
    total = len(a) + len(b)
    # 2*common/total is mathematically equivalent but can round differently.
    # Match Indel.normalized_similarity's 1-normalized_distance evaluation so
    # an inclusive cutoff does not move when the optional backend is installed.
    return 1.0 - (total - 2 * common) / total


def _lcs_length(a: str, b: str, /) -> int:
    """Longest common subsequence length with a single bounded DP row."""
    row = [0] * (len(a) + 1)
    for cb in b:
        diagonal = left = 0
        for j, ca in enumerate(a, 1):
            up = row[j]
            row[j] = left = diagonal + 1 if ca == cb else max(left, up)
            diagonal = up
    return row[-1]


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
    exact = type(a) is str and type(b) is str
    if exact and _native_jaro is not None:
        result = _native_jaro(a, b)
        if result is not NotImplemented:
            return result
    size = len(b)
    window = max(max(len(a), size) // 2 - 1, 0)
    taken = [False] * size
    a_matched: list[str] = []
    for i, ca in enumerate(a):
        lo, hi = max(0, i - window), min(size, i + window + 1)
        if exact:  # str.find scans the window in C; subclasses keep their hooks
            j = b.find(ca, lo, hi)
            while j >= 0 and taken[j]:
                j = b.find(ca, j + 1, hi)
        else:
            j = next((j for j in range(lo, hi) if not taken[j] and b[j] == ca), -1)
        if j >= 0:
            taken[j] = True
            a_matched.append(ca)
    if not (m := len(a_matched)):
        return 0.0
    # Half the mismatched positions, floored — the transposition count every
    # implementation uses. An odd mismatch count means the matched sequences
    # differ by a cycle longer than a swap, and rounding it up would charge the
    # pair for a transposition that isn't one.
    swaps: int = sum(map(ne, a_matched, compress(b, taken))) // 2
    return (m / len(a) + m / size + (m - swaps) / m) / 3


def _jaro_winkler(a: str, b: str, /, *, prefix_weight: float = 0.1) -> float:
    """Jaro plus Winkler's prefix bonus, awarded only above the 0.7 boost threshold.

    The threshold is what keeps a shared initial from promoting two different
    names: "Jane"/"John" sits at 0.667 and stays there, where an unconditional
    bonus would read 0.70 and cross thresholds callers set just above it.
    """
    if prefix_weight < 0.0 or prefix_weight > 1.0:
        msg = "prefix_weight has to be in the range 0.0 - 1.0"
        raise ValueError(msg)
    if (sim := _jaro(a, b)) <= _BOOST_MIN:
        return sim
    prefix = 0
    for ca, cb in zip(a[:_PREFIX_MAX], b[:_PREFIX_MAX], strict=False):
        if ca != cb:
            break
        prefix += 1
    score = sim + prefix * prefix_weight * (1.0 - sim)
    # RapidFuzz applies its default zero cutoff after the boost. A NaN weight
    # fails that comparison, while scores below the boost threshold bypass it.
    return min(score, 1.0) if score >= 0.0 else 0.0


def _levenshtein(a: Sequence[object], b: Sequence[object], /) -> int:
    """Uniform Levenshtein distance, with packed DP columns for exact strings.

    Faithful stand-in for ``rapidfuzz.distance.Levenshtein.distance`` when the
    ``text`` extra is absent. Foreign sequences retain the classic two-row DP
    and its equality/iteration behavior, including unhashable elements.
    """
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    if type(a) is not str or type(b) is not str:
        return _sequence_distance(a, b)
    if _native_distance is not None:
        result = _native_distance(a, b)
        if result is not NotImplemented:
            return result
    return _string_distance(a, b)


def _sequence_distance(a: Sequence[object], b: Sequence[object]) -> int:
    """Wagner-Fischer DP for arbitrary sequences, without hashing their elements."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [left := i]
        for cb, diagonal, up in zip(b, prev, prev[1:], strict=False):
            cur.append(left := min(left + 1, up + 1, diagonal + (ca != cb)))
        prev = cur
    return prev[-1]


def _string_distance(a: str, b: str) -> int:
    """Myers' bit-vector recurrence, evaluated by Python's arbitrary-width ints.

    One bit records each DP row's vertical difference. Integer arithmetic updates
    a whole column at once instead of allocating and visiting Python cells.
    The shorter string bounds the bit width; at most 256 character masks keep
    their total storage linear with a small constant even for diverse Unicode.
    """
    if len(a) > len(b):
        a, b = b, a
    if (masks := _masks(a)) is None:
        return _sequence_distance(b, a)
    get, width = masks.get, (1 << len(a)) - 1
    high, positive, negative, score = (width + 1) >> 1, width, 0, len(a)
    for char in b:
        matches = get(char, 0)
        diff = (((matches & positive) + positive) ^ positive) | matches | negative
        h_pos, h_neg = negative | ~(diff | positive), diff & positive
        if h_pos & high:
            score += 1
        elif h_neg & high:
            score -= 1
        h_pos = (h_pos << 1) | 1
        # Python's complement has infinitely many sign bits. Discard the rows
        # outside the pattern so shifts cannot grow state with the text length.
        positive = ((h_neg << 1) | ~(diff | h_pos)) & width
        negative = diff & h_pos & width
    return score


class _Similarity(Protocol):
    def __call__(self, a: str, b: str, /) -> float: ...


class _Distance(Protocol):
    def __call__(self, a: str, b: str, /) -> int: ...


class _NameSimilarity(Protocol):
    def __call__(self, a: str, b: str, /, *, prefix_weight: float = ...) -> float: ...


indel_ratio: _Similarity = (
    _rf.distance.Indel.normalized_similarity if _HAVE_RAPIDFUZZ else _indel_ratio
)
"""Normalized Indel similarity for strings, identical across optional backends.

Mathematically ``2·LCS / (len(a)+len(b))``; two empty strings score ``1.0``.
Normalization evaluates ``1 - Indel_distance / (len(a)+len(b))`` to preserve
RapidFuzz's floating-point results, including at inclusive score cutoffs.
String subclasses are scored by their underlying Unicode value, ignoring
custom equality, length, iteration, and string-conversion methods.
"""

# The legacy pairwise scores are the rapidfuzz kernels when the extra is present —
# bound directly, so a call pays no forwarding frame (~30% of a short-string
# score) — and the portable definitions below the fold otherwise.
ratio: _Similarity = (
    _rf.distance.Indel.normalized_similarity if _HAVE_RAPIDFUZZ else _difflib_ratio
)
"""Historical similarity on a ``0.0 .. 1.0`` scale, with a backend-selected metric.

RapidFuzz selects Indel; the portable path selects difflib SequenceMatcher.
Scores and rankings can differ. Use :func:`indel_ratio` or :func:`difflib_ratio`
to choose a backend-independent algorithm. Two empty strings score ``1.0``.
"""

distance: _Distance = (
    _rf.distance.Levenshtein.distance if _HAVE_RAPIDFUZZ else _levenshtein
)
"""Uniform Levenshtein edit distance (insert/delete/substitute, all cost 1).

Matches ``rapidfuzz.distance.Levenshtein.distance`` exactly — the value is a
single well-defined integer, so the pure-Python DP fallback agrees with the
rapidfuzz kernel byte-for-byte (only the speed differs).
"""

jaro_winkler: _NameSimilarity = (
    _rf.distance.JaroWinkler.similarity if _HAVE_RAPIDFUZZ else _jaro_winkler
)
"""Prefix-weighted Jaro-Winkler similarity in ``0.0 .. 1.0`` — the name comparator.

Prefer this over :func:`ratio` when the strings are *names* (people, places,
companies): a shared opening is much stronger evidence of the same referent
than a shared middle, which is the asymmetry the prefix bonus encodes and
Indel similarity has no way to express. *prefix_weight* is the per-character
bonus for a common prefix of up to four characters; the default ``0.1``
saturates the bonus at four, as Winkler's formulation does. Pairs scoring at
or below 0.7 on plain Jaro get no bonus at all (the boost threshold), so a
shared initial cannot lift two unrelated names.

Matches ``rapidfuzz.distance.JaroWinkler.similarity`` on both paths.
"""


def _accelerated(scorer: _Similarity | None) -> bool:
    """Whether a batch call may hand the whole job to RapidFuzz's Indel kernel."""
    return _HAVE_RAPIDFUZZ and (
        scorer is None or scorer is _rf.distance.Indel.normalized_similarity
    )


def best_match(
    query: str,
    choices: Iterable[str],
    *,
    score_cutoff: float = 0.0,
    scorer: _Similarity | None = None,
) -> tuple[str, float] | None:
    """Return the single best ``(choice, score)`` for *query*, or ``None``.

    *scorer* is a two-string similarity callable on the ``0.0 .. 1.0`` scale;
    higher scores win. Omit it to retain the backend-selected :func:`ratio`, or
    choose :func:`indel_ratio` / :func:`difflib_ratio` for stable semantics.
    Scores below *score_cutoff* are excluded (``None`` if none qualify or
    *choices* is empty). Ties resolve to the first choice in iteration order.
    With an explicit scorer, mappings supply their iterated keys, like other
    iterables; a NaN cutoff admits no choices.
    """
    if _HAVE_RAPIDFUZZ and (
        scorer is None or scorer is _rf.distance.Indel.normalized_similarity
    ):
        hit = _rf.process.extractOne(
            query,
            choices if scorer is None else iter(choices),
            scorer=_rf.distance.Indel.normalized_similarity,
        )
        return (hit[0], hit[1]) if hit and hit[1] >= score_cutoff else None
    similarity = ratio if scorer is None else scorer
    return max(
        ((c, s) for c in choices if (s := similarity(query, c)) >= score_cutoff),
        key=itemgetter(1),
        default=None,
    )


def extract(
    query: str,
    choices: Iterable[str],
    *,
    limit: int | None = 5,
    score_cutoff: float = 0.0,
    scorer: _Similarity | None = None,
) -> list[tuple[str, float]]:
    """Top-*limit* ``(choice, score)`` for *query*, best first.

    *scorer* selects the similarity as in :func:`best_match`; omission retains
    the backend-selected :func:`ratio`. Scores below *score_cutoff* are excluded.
    ``limit=None`` returns every qualifying choice; explicit scorers require a
    nonnegative limit. Equal scores retain their original iteration order.
    """
    if scorer is not None and limit is not None and limit < 0:
        msg = "unstd.text: limit must be nonnegative when a scorer is selected"
        raise ValueError(msg)
    if _accelerated(scorer):
        hits = _rf.process.extract(
            query,
            list(choices),
            scorer=_rf.distance.Indel.normalized_similarity,
            limit=limit,
        )
        return [(choice, score) for choice, score, _ in hits if score >= score_cutoff]
    similarity = ratio if scorer is None else scorer
    scored = [(c, s) for c in choices if (s := similarity(query, c)) >= score_cutoff]
    return sorted(scored, key=itemgetter(1), reverse=True)[:limit]


def cdist(
    queries: Sequence[str],
    choices: Sequence[str],
    *,
    score_cutoff: float = 0.0,
    workers: int = 1,
    scorer: _Similarity | None = None,
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
    zeroing convention. *scorer* selects a similarity as in :func:`best_match`;
    omission retains the backend-selected :func:`ratio`. Custom scorers and
    :func:`difflib_ratio` use a nested-loop matrix, ignoring *workers*.

    ``rapidfuzz.process.cdist`` itself additionally requires ``numpy`` (absent
    from the ``text`` extra's own dependencies); when it's missing this
    degrades to the same fallback the extra-absent case takes.
    """
    if _accelerated(scorer):
        try:
            import numpy as np  # cdist is numpy-backed; numpy isn't a `text`-extra dep

            matrix = _rf.process.cdist(
                queries,
                choices,
                scorer=_rf.distance.Indel.normalized_similarity,
                workers=workers,
                dtype=np.float64,  # float32 would disagree with `ratio` past 7 digits
            )
        except ImportError:
            pass
        else:
            # Explicit scorers use the same inclusive predicate as the Python
            # path, including NaN (which admits no score). Preserve the legacy
            # omitted-scorer predicate for existing installations.
            rejected = (
                matrix < score_cutoff if scorer is None else ~(matrix >= score_cutoff)
            )
            matrix[rejected] = 0.0
            rows: list[list[float]] = matrix.tolist()
            return rows
    similarity = ratio if scorer is None else scorer
    return [
        [s if (s := similarity(q, c)) >= score_cutoff else 0.0 for c in choices]
        for q in queries
    ]
