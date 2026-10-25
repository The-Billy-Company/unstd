# Native gzip reads

Private build inputs for `unstd.compress._gzip_native`. Start with
[`gzipx.py`](../../src/unstd/compress/gzipx.py) for public dispatch and
[`_reader.py`](../../src/unstd/compress/engines/reader.py) for streaming integration.

| File | Responsibility |
| --- | --- |
| [`src/adapter.c`](src/adapter.c) | Module initialization, whole-buffer gzip decoding, and CRC32 |
| [`src/stream.c`](src/stream.c) | Python `Inflater` object, retained input, bounded output, and EOF state |
| [`src/inflate.c`](src/inflate.c) | zlib ABI calls and checked Python RAW allocation callbacks |
| [`src/stream.h`](src/stream.h) | Shared inflater declarations and module-registration entry point |

Buffer decoding accepts exact `bytes` and returns `None` for stdlib replay when
framing, checksums, size, or input consumption cannot be accepted; allocation
errors propagate. Streaming feeds raw DEFLATE through `stream.c` and `inflate.c`;
the running interpreter's gzip reader owns headers, trailers, member boundaries,
and error-delivery order. See [`src/README.md`](src/README.md) before changing
buffer ownership or read budgets.

[`hatch_build.py`](../../hatch_build.py) compiles vendored libdeflate 1.25 for
buffer decoding and CRC32. Streaming uses platform `libz` on macOS and vendored
zlib 1.3.2 with `Z_PREFIX` elsewhere. [`vendor/README.md`](vendor/README.md)
maps those source closures and licenses. Default builds need no Cargo,
source downloads, or system libdeflate.

[`experiments/README.md`](experiments/README.md) covers the isolated
`qualify_libdeflate.py`, `libdeflate-1.26.json`, and `zlib-rs-0.6.7.json`
qualification inputs; none changes the default backend.
