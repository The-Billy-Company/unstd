# Text matching contracts

Contracts for `unstd.text.fuzz`: similarity scores, edit distance, ranking, portable Unicode behavior, and native kernels.

- [unit/](unit/README.md): scoring APIs, independent edit-distance reference, native safety edges, and batch boundaries.
- [properties/](properties/README.md): generated metric laws against textbook references on portable, native and RapidFuzz paths, plus batch selection and all-pairs laws.

Portable behavior runs without extras. RapidFuzz comparisons require the `text` extra; native kernel tests require the compiled text extension.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/text -q
```

See [the module documentation](../../../../src/unstd/text/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
