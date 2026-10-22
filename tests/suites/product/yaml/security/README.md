# YAML input boundaries

`test_yaml_boundary.py` checks that safe/editable entry points refuse attacker-selected constructors and duplicate semantic keys, while valid merge/alias controls remain usable. Depth, encoding, and alias probes use bounded workers and avoid expanding alias graphs merely to inspect them.

`test_merge_duplicates.py` retains duplicate explicit-key cases hidden by merge keys, alongside valid precedence and explicit-override controls.

`test_resource_corpus.py` extends the alias probe to the editable reader and the writer, and adds deep flow and block nesting, wide collections, long scalars and explicit keys, many anchors, and the YAML 1.2 1024-character implicit-key limit. Large inputs run in killable workers that may only complete or raise a permitted rejection; each shape has a small neighbour checked exactly in process.

`test_malformed_bytes.py` rejects invalid UTF-8, raw NUL and control characters, tab indentation, unterminated quotes and flows, unknown escapes and undefined aliases with ruamel's error class and a hand-counted mark across all four readers. Each case has a valid neighbour with an exact value. UTF-8/UTF-16 byte-order marks and the full double-quoted escape table are covered as accepting controls.

These contracts require ruamel.yaml from the `yaml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml/security -q
```

See [the module test overview](../README.md) for related evidence.
