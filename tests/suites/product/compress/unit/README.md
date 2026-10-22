# Gzip API and native edge contracts

`test_gzipx.py` compares gzip values, meaningful headers, error families, file modes, and caller-owned streams with the running stdlib. It includes real compressed NDJSON pipelines and consumer typing checks. Compressed byte identity across engines is not required.

`test_rfc1952_layout.py` hand-packs header, FNAME and `CRC32 || ISIZE` trailer fields at every level. It checks fixed-mtime determinism, empty and mixed-level members, and damage in a later member against stdlib's delivered bytes and exception class.

`test_gzipx_edges.py` checks native inflater limits, trailing bytes, short reads, callback errors, and cleanup. Native cases require zlib-ng; typed pipelines require msgspec and typing checks require mypy.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/compress/unit -q
```

See [the module test overview](../README.md) for related evidence.
