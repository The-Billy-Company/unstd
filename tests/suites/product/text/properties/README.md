# Text matching properties

`test_metric_laws.py` generates surrogate-free Unicode with astral characters and combining marks, plus repeated strings. Independent textbook references (Wagner-Fischer Levenshtein, LCS-based Indel, and Jaro-Winkler from its published definition) are compared on the portable Python bodies with native kernels disabled, on the bundled native kernels, and on RapidFuzz. Levenshtein must also satisfy identity, symmetry, the triangle inequality and its length bounds.

`test_batch_laws.py` applies the documented selection rule (inclusive cutoff, descending score, ties in input order, then `limit`) to `extract` and `best_match` over lists and one-shot iterators, and holds every `cdist` cell to its scalar scorer, for Indel, difflib and the omitted scorer.

Portable cases need no extras. Native cases skip when the compiled extension is absent; RapidFuzz cases skip locally without the `text` capability and fail under `--require-extras`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/text/properties -q
```

See [the module test overview](../README.md) for related evidence.
