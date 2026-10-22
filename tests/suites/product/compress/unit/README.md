# Gzip API and native edge contracts

`test_gzipx.py` compares gzip values, meaningful headers, error families, file modes, and caller-owned streams with the running stdlib. It includes real compressed NDJSON pipelines and consumer typing checks. Compressed byte identity across engines is not required.

`test_gzipx_edges.py` checks native inflater limits, trailing bytes, short reads, callback errors, and cleanup. Native cases require zlib-ng; typed pipelines require msgspec and typing checks require mypy.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/compress/unit -q
```

See [the module test overview](../README.md) for related evidence.
