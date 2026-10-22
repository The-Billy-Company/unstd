# Text scoring and native kernel contracts

`test_text.py` checks score scales, fallback difflib behavior, RapidFuzz differences, ranking, and cutoffs. `test_distance.py` compares edit distance with an independent full cost matrix. `test_native.py` checks Unicode kernels, supported inputs, bounded work/refusal, and agreement with portable results.

Portable cases run without extras. RapidFuzz comparisons require the `text` capability; compiled kernel tests skip when the native extension is absent.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/text/unit -q
```

See [the module test overview](../README.md) for related evidence.
