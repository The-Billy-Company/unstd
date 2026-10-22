# Text scoring and native kernel contracts

`test_text.py` checks score scales, fallback difflib behavior, RapidFuzz differences, ranking, cutoffs, and Jaro-Winkler weight boundaries/NaN behavior. `test_distance.py` compares edit distance with an independent full cost matrix. `test_native.py` checks Unicode kernels, supported inputs, bounded work/refusal, and agreement with portable results.

`test_stable_scorers.py` compares named algorithms with an independent edit matrix and RapidFuzz, then checks explicit-scorer ranking, ties, cutoffs, Unicode, empty inputs, and NumPy-free batching in both backend configurations.

`test_batch_boundaries.py` pins hand-derived ties with duplicates at every `limit`, cutoffs one float above and below a score, one-shot iterator choices, `cdist` workers on the batch kernel and Python paths, large all-pairs matrix shape/element types, and the documented difflib `autojunk` divergence from Indel.

Portable cases run without extras. RapidFuzz comparisons require the `text` capability; compiled kernel tests skip when the native extension is absent.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/text/unit -q
```

See [the module test overview](../README.md) for related evidence.
