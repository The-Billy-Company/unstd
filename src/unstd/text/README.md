---
doc_radar:
  sentinels:
    - file: src/unstd/text/fuzz.py
      contains: ['from unstd.text._text_native import distance as _native_distance', 'def best_match(']
---

# `unstd.text`

String similarity, fuzzy matching, Levenshtein distance, and Jaro–Winkler name
scoring live in [`fuzz.py`](fuzz.py). Similarity scores use a `0.0 .. 1.0` scale.

```python
from unstd.text import fuzz

fuzz.indel_ratio("abcbc", "bcabc")  # 0.8, independent of installed extras
fuzz.difflib_ratio("abcbc", "bcabc")  # 0.6, always stdlib SequenceMatcher
fuzz.best_match("aple", ["apple", "grape"], scorer=fuzz.indel_ratio)
# ("apple", approximately 0.8889)
fuzz.extract("aple", ["apple", "grape"], limit=2)  # best-first (choice, score) pairs
fuzz.distance("kitten", "sitting")  # 3
fuzz.jaro_winkler("Jon", "Jonathan")  # approximately 0.8542
fuzz.cdist(["aple", "bananna"], ["apple", "banana"])  # list[list[float]]
```

## Source ownership

This flat leaf keeps scorer selection, ranking, and batch behavior in the same
public module so every matching API shares its metric policy.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Package documentation; callers import the `fuzz` module |
| [`fuzz.py`](fuzz.py) | Similarity/distance algorithms, optional backend selection, matching, ranking, and batch scoring |
| [`_text_native.pyi`](_text_native.pyi) | Native ABI declarations; [kernel source ownership](../../../native/text/README.md) |

The [validation guide](#validation) gives the current product-suite run path.

## Backends and score differences

Install `unstd[text]` for RapidFuzz. Without that extra, `ratio` uses
`difflib.SequenceMatcher(None, a, b).ratio()`. RapidFuzz uses normalized Indel
similarity, `2 * LCS / (len(a) + len(b))`, with two empty strings scoring `1.0`.
The metrics often agree but can produce different scores and rankings. The
fallback also retains difflib's `autojunk` heuristic, which Indel does not use.
Existing calls retain that selection for compatibility. Choose an explicit
algorithm when dependencies must not change scores, rankings, or cutoff decisions:

- `indel_ratio(a, b)` always computes normalized Indel similarity. RapidFuzz
  accelerates it when installed; the portable implementation uses bit-vector LCS
  with bounded mask storage and a DP fallback for diverse Unicode. Both evaluate
  `1 - Indel_distance / (len(a) + len(b))` to preserve exact float results at
  score cutoffs, with two empty strings scoring `1.0`.
- `difflib_ratio(a, b)` always calls stdlib `SequenceMatcher`, retaining its
  `autojunk` heuristic and order-sensitive block matching.

The named scorer contracts apply to strings. Indel reads the underlying Unicode
value of string subclasses; difflib retains stdlib's handling of those objects.
No normalization, case conversion, or Unicode grapheme segmentation is performed.

`best_match` returns the highest-scoring `(choice, score)` or `None`;
`extract` returns ranked pairs, capped by `limit` (`None` means all). Both filter
with an inclusive `score_cutoff`. `cdist` returns a nested Python list with rows
for queries and columns for choices; scores below the cutoff become `0.0`.
With RapidFuzz and NumPy it uses the batch kernel, forwarding `workers`
(`-1` selects all cores). Otherwise it loops over the currently selected
`ratio`, so its metric still depends on whether RapidFuzz is installed. NumPy
is an additional requirement for the batch kernel and is not included in the
`text` extra.

Each helper also accepts `scorer=`: pass `fuzz.indel_ratio` or
`fuzz.difflib_ratio` to keep that algorithm on every installation. A custom scorer
must accept two strings and return a similarity on `0.0 .. 1.0`, with larger
values better. Indel retains RapidFuzz's optimized matching and batch kernels
where available; difflib and custom scorers use Python iteration (`workers` is
ignored). Equal scores keep choice order. With an explicit scorer, `extract`
requires a nonnegative `limit` or `None`. Omitting `scorer` retains existing
behavior, including the backend-selected metric. Explicit scorers consume
mappings as iterables of keys and honor their iteration order. With an explicit
scorer, every helper uses the inclusive `score >= score_cutoff` predicate: a NaN cutoff retains no
scores, positive infinity retains none, and negative infinity retains all.

```python
choices = ["bcabc", "abccc"]
fuzz.extract("abcbc", choices, scorer=fuzz.indel_ratio)  # both score 0.8
fuzz.extract("abcbc", choices, scorer=fuzz.difflib_ratio)  # abccc wins, 0.8 vs 0.6
fuzz.cdist(["abcbc"], choices, scorer=fuzz.indel_ratio, score_cutoff=0.8)
# [[0.8, 0.8]]; scores equal to the cutoff are retained
```

`distance` counts uniform Levenshtein edits. `jaro_winkler` accepts
`prefix_weight=0.1` and applies a prefix bonus only above a Jaro score of `0.7`.
Their portable implementations are tested against RapidFuzz; they do not use
difflib's metric.

## Bundled native kernels

CPython builds optionally include `_text_native`, which accelerates exact-string
Levenshtein and Jaro calculations on the base-install paths. Unsupported shapes
or work beyond its internal bounds defer to Python; string subclasses and
non-string sequences retain their Python behavior. Builds without the extension
also work. Installing RapidFuzz selects its public distance and name scorers.

Edit distance removes equal prefixes and suffixes before comparing the
remaining middle. This preserves the exact edit count and makes long,
near-identical strings inexpensive on both the bundled and pure-Python paths.
Native work limits apply to the remainder, with a separate bounded scan of
the shared ends; native views allocate no substrings. See the
[before/after evidence](../../../bench/reports/text/affixes.md) for workload
shapes, controls, timing samples, and reproduction commands. These improvements
apply when RapidFuzz is absent; its installed public binding stays unchanged.

The C implementation lives in [`native/text/src/adapter.c`](../../../native/text/src/adapter.c), with signatures
in [`_text_native.pyi`](_text_native.pyi). An opt-in
[Rust experiment](../../../native/text/README.md) implements the same kernel
boundary. [Measurements](../../../bench/reports/native/native-results.md) document workloads
and fallback costs.

## Validation

[`tests/suites/product/text/`](../../../tests/suites/product/text/README.md)
covers scoring, cutoffs, backend differences, distance invariants, Unicode,
native work limits, and fallback dispatch. From the repository root, run
`uv run --no-sync pytest tests/suites/product/text -q`.
