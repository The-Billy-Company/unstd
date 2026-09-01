# `unstd.text`

Fuzzy string similarity and matching — a stdlib-`difflib`-faithful surface backed
by [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz) (Max Bachmann) on the
fast path. Part of [`unstd`](../README.md).

## Modules

| File      | Role                                                                                                                                                       |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fuzz.py` | `ratio` / `best_match` / `extract` / `distance` / `jaro_winkler` / `cdist`. `difflib`-scale similarity + Levenshtein distance + the name comparator + the batch matrix. `text` extra → `rapidfuzz`; stdlib fallback otherwise. |

## Backend

The `text` extra (`pip install 'unstd[text]'`) provides `rapidfuzz>=3.12.0`.
`rapidfuzz` computes the same class of
score with a bit-parallel C++/SIMD Indel kernel — an order of magnitude faster
than `difflib`'s pure-Python O(n·m) Ratcliff–Obershelp on the hot path. Without
the extra every function falls back to the stdlib: `difflib` for
`ratio`/`best_match`/`extract`, a pure-Python Wagner–Fischer DP for `distance` —
same surface, slower.

```python
from unstd.text import fuzz

fuzz.ratio("kitten", "sitting")  # 0.0 .. 1.0  (difflib scale)
fuzz.best_match("aple", ["apple", "grape"])  # ("apple", 0.888…) | None
fuzz.extract("aple", choices, limit=3)  # [(choice, score), …] best-first
fuzz.distance("kitten", "sitting")  # 3  (uniform Levenshtein edits)
fuzz.jaro_winkler("Jon", "Jonathan")  # 0.925 — prefix-weighted, for names
fuzz.cdist(["aple", "bananna"], choices)  # N×M matrix, one call — entity resolution
```

## Faithfulness — scale, semantics, and the pinned divergence

`ratio` is a drop-in for `difflib.SequenceMatcher(None, a, b).ratio()`:

- **Scale.** `difflib` scores `0.0 .. 1.0`; `rapidfuzz.fuzz.ratio` scores
  `0.0 .. 100.0`. This module normalizes the fast path back to `0.0 .. 1.0` (via
  `rapidfuzz.distance.Indel.normalized_similarity`), so `ratio` and every
  `score_cutoff` argument are on difflib's `0.0 .. 1.0` scale on both paths.
- **Metric.** The fast path uses **normalized Indel similarity** —
  `2·LCS / (len(a) + len(b))` — the metric `rapidfuzz.fuzz.ratio` is built on and
  the closest faithful analogue of difflib's ratio. difflib's Ratcliff–Obershelp
  counts characters in the _longest contiguous_ matching block recursively, whose
  total `M ≤ LCS`. So on the fast path `ratio ≥ SequenceMatcher.ratio` for all
  inputs, **with equality on the overwhelming majority** (identical, disjoint,
  and typical single-edit strings all agree exactly).

| Input pair                     | `difflib` ratio | Indel (fast path) | Agree?                 |
| ------------------------------ | --------------- | ----------------- | ---------------------- |
| `"kitten"` / `"sitting"`       | 0.6154          | 0.6154            | yes                    |
| `"apple"` / `"aple"`           | 0.8889          | 0.8889            | yes                    |
| identical / identical          | 1.0             | 1.0               | yes                    |
| disjoint (`"abc"`/`"xyz"`)     | 0.0             | 0.0               | yes                    |
| `"mississippi"` / `"mssiippi"` | <               | greater           | **no** (gestalt < LCS) |

The last row is the pinned divergence: where the greedy gestalt block choice
underperforms the LCS, Indel scores higher. The **fallback path (difflib itself)
is always exact**; the fast path is not byte-identical to difflib on those
adversarial cases. `difflib`'s `autojunk` heuristic (popular-character junking on
sequences ≥200 long) is another fast-path divergence — the fallback preserves it,
the Indel path does not model it. Both are pinned as regression guards in
`tests/text/test_text.py` and are never shipped silently.

`distance` is the uniform Levenshtein edit distance (weights `(1, 1, 1)`),
matching `rapidfuzz.distance.Levenshtein.distance`. Levenshtein distance is a
single well-defined integer, so the pure-Python fallback agrees with the
rapidfuzz kernel **byte-for-byte** — only the speed differs.

## Batch matching — `cdist`

`best_match`/`extract` answer "match this one query" — scoring *N* queries
against *M* choices by calling either N times pays N separate Python-level
scans. `cdist` computes the whole N×M similarity matrix in a single call,
riding `rapidfuzz.process.cdist`'s SIMD kernel — the single biggest win
rapidfuzz's own benchmarks report for many-query fuzzy matching (entity
resolution, contact dedup, matching an inbound roster against an existing
one). `cdist` itself needs `numpy` in addition to the `text` extra (rapidfuzz's
own dependency for that one function); absent either, it degrades to the same
nested-loop-over-`ratio` fallback as everything else here — same numbers, no
batch speedup.

## Prior art

- [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz) (Max Bachmann) — the
  C++/SIMD backend; its `api_differences.md` documents the Indel-vs-difflib
  choice this module follows.
- `difflib.SequenceMatcher` — Ratcliff–Obershelp gestalt pattern matching
  (Ratcliff & Metzener, _Dr. Dobb's Journal_, July 1988); the fallback and the
  faithfulness target.
- Wagner & Fischer, "The String-to-String Correction Problem" (_JACM_, 1974) —
  the two-row DP the `distance` fallback implements.
