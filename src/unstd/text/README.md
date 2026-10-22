# `unstd.text`

String similarity, fuzzy matching, Levenshtein distance, and Jaro–Winkler name
scoring live in [`fuzz.py`](fuzz.py). Similarity scores use a `0.0 .. 1.0` scale.

```python
from unstd.text import fuzz

fuzz.ratio("kitten", "sitting")  # approximately 0.6154
fuzz.best_match("aple", ["apple", "grape"])  # ("apple", approximately 0.8889)
fuzz.extract("aple", ["apple", "grape"], limit=2)  # best-first (choice, score) pairs
fuzz.distance("kitten", "sitting")  # 3
fuzz.jaro_winkler("Jon", "Jonathan")  # approximately 0.8542
fuzz.cdist(["aple", "bananna"], ["apple", "banana"])  # list[list[float]]
```

## Backends and score differences

Install `unstd[text]` for RapidFuzz. Without that extra, `ratio` uses
`difflib.SequenceMatcher(None, a, b).ratio()`. RapidFuzz uses normalized Indel
similarity, `2 * LCS / (len(a) + len(b))`, with two empty strings scoring `1.0`.
The metrics often agree but can produce different scores and rankings. The
fallback also retains difflib's `autojunk` heuristic, which Indel does not use.
This is a shared scale and API, rather than exact difflib compatibility across
backends.

`best_match` returns the highest-scoring `(choice, score)` or `None`;
`extract` returns ranked pairs, capped by `limit` (`None` means all). Both filter
with an inclusive `score_cutoff`. `cdist` returns a nested Python list with rows
for queries and columns for choices; scores below the cutoff become `0.0`.
With RapidFuzz and NumPy it uses the batch kernel, forwarding `workers`
(`-1` selects all cores). Otherwise it loops over the currently selected
`ratio`, so its metric still depends on whether RapidFuzz is installed. NumPy
is an additional requirement for the batch kernel and is not included in the
`text` extra.

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

The C implementation lives in [`_text_native.c`](_text_native.c), with signatures
in [`_text_native.pyi`](_text_native.pyi). An opt-in
[Rust experiment](../../../native/text/README.md) implements the same kernel
boundary. [Measurements](../../../bench/native-results.md) document workloads
and fallback costs.

## Validation

[`tests/suites/product/text/`](../../../tests/suites/product/text/README.md)
covers scoring, cutoffs, backend differences, distance invariants, Unicode,
native work limits, and fallback dispatch. From the repository root, run
`uv run --no-sync pytest tests/suites/product/text -q`.
