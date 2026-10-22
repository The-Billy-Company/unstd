# Gzip stream properties

`test_gzip_laws.py` generates payloads, levels and partitions. Partitioned `GzipFile` writes must form one stdlib-decodable member with a `CRC32 || ISIZE` trailer. Partitioned reads return at most the requested size and exactly the payload. Every proper prefix of a member is rejected with stdlib's exception class, and concatenated members decode to the concatenated payloads.

The laws run against whichever gzip backend is installed, with stdlib `gzip`/`zlib` as the oracle.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/compress/properties -q
```

See [the module test overview](../README.md) for related evidence.
