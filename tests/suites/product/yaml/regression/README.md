# YAML regressions

`test_merge_amplification.py` retains a merge-key amplification: a 2 KB document merging the previous anchor ten times per level made the safe readers copy 10\*\*depth key/value pairs, so depth 12 never finished inside the bounded worker's deadline. Fan-out and chained merges now complete with the exact key union; precedence, key order and backend parity are checked on small documents, and duplicate explicit keys still reject.

`test_editable_emission.py` retains two writer defects found by the generated round trips. Strings containing NEL (U+0085) were single-quoted, and the backend reader folded NEL into a space. Parsed exponent floats such as `3.403832445554453e+16` were re-rendered by truncating the mantissa, writing a different number. Expected values are Python literals, and a plain ruamel safe loader reads each output too.

These contracts require ruamel.yaml from the `yaml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml/regression -q
```

See [the module test overview](../README.md) for related evidence.
