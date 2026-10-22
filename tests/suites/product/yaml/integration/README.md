# YAML stream and isolation boundaries

`test_stream_faults.py` uses fixture-owned text and binary handles that fail on a chosen read, write or flush. The injected exception object must escape unchanged, `load` returns nothing, `load_all` yields only an exact prefix and then ends, writers leave a prefix of the healthy output, and the module never closes the handle. Document-source and representer failures, real file handles, and recovery on the next call are covered as well.

`test_parser_isolation.py` interleaves `load_all` iterators that reuse anchor names, mixes editable and safe reads, re-enters the writer from a `dump_all` generator, and pauses writers and iterators across threads at a barrier. Anchors stay private to their document (YAML 1.2 §9.2), editable options never leak into safe values, and every stream's output is exact.

These contracts require ruamel.yaml from the `yaml` extra.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/yaml/integration -q
```

See [the module test overview](../README.md) for related evidence.
