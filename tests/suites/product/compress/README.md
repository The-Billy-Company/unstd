# Gzip contracts

Contracts for `unstd.compress.gzipx`: stdlib-compatible gzip files and streams, accelerated codec edges, and compressed NDJSON pipelines.

- [unit/](unit/README.md): gzip interoperability, I/O ownership, errors, and native stream edges.

The suite exercises the stdlib fallback and the zlib-ng path when installed. Typed pipeline and consumer typing cases also need msgspec and mypy respectively.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/compress -q
```

See [the module documentation](../../../../src/unstd/compress/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
