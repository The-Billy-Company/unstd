# Native gzip codecs

Private build inputs for `unstd.compress._gzip_native`. Start with
[`gzipx.py`](../../src/unstd/compress/gzipx.py) for public dispatch and
[`engines/reader.py`](../../src/unstd/compress/engines/reader.py) for streaming integration.

| File | Responsibility |
| --- | --- |
| [`src/adapter.c`](src/adapter.c) | Module initialization, whole-buffer gzip encoding/decoding, and CRC32 |
| [`src/fast.c`](src/fast.c), [`src/fast.h`](src/fast.h) | Owned three-candidate HT matchfinder around the unchanged upstream compressor |
| [`src/stream.c`](src/stream.c) | Python `Inflater` object, retained input, bounded output, and EOF state |
| [`src/inflate.c`](src/inflate.c) | zlib ABI calls and checked Python RAW allocation callbacks |
| [`src/stream.h`](src/stream.h) | Shared inflater declarations and module-registration entry point |
| [`src/module.h`](src/module.h) | Per-module codec ownership shared by the C adapters |
| [`src/shape.h`](src/shape.h) | Bounded structural probe for Mac ARM streaming selection |

Buffer decoding accepts exact `bytes` and returns `None` for stdlib replay when
framing, checksums, size, or input consumption cannot be accepted; allocation
errors propagate. Streaming feeds raw DEFLATE through `stream.c` and `inflate.c`;
the running interpreter's gzip reader owns headers, trailers, member boundaries,
and error-delivery order. See [`src/README.md`](src/README.md) before changing
buffer ownership or read budgets.

Whole-buffer encoding admits exact `bytes`, public levels 6 and 9, and integer
`mtime=0`. Conservative byte-distribution and periodicity guards preserve the
existing zlib-ng route for unsuitable inputs. Level 6 additionally requires a
compact ASCII JSON-shaped prefix and maps to native level 1 with the owned
three-candidate HT matchfinder; level 9 maps to native level 6. Unsupported
arguments and workloads retain the existing provider. Native allocation or
compression failures propagate without replay. Stream writers and compressor
copies retain their existing provider.

[`hatch_build.py`](../../hatch_build.py) compiles vendored libdeflate 1.25 for
buffer encoding/decoding and CRC32. The private adapter links platform `libz` on
macOS and vendored zlib 1.3.2 with `Z_PREFIX` elsewhere. The public reader selects
zlib-ng's native raw inflater on Linux while retaining the adapter on macOS and
other platforms. Both keep the interpreter's gzip framing and native CRC.
On Mac ARM, a bounded complete-block probe can select the already loaded
zlib-ng provider for short-distance repetitive streams before decoding begins.
Ordinary dataset streams retain Apple libz; Linux retains its native SIMD path.
[`vendor/README.md`](vendor/README.md)
maps those source closures and licenses. Default builds need no Cargo,
source downloads, or system libdeflate.

Linux x86_64 builds can include a private ISA-L 2.31.1 decoder assembled locally
with NASM or Yasm. The build verifies the reviewed vendored sources, copies them
to a private build directory, and applies the owned bounded-read and error
corrections there. Conservative CPU and stream-shape admission governs decoder
selection; other platforms and ineligible streams retain their existing routes.
See [`src/README.md`](src/README.md) for source and symbol ownership.

[`experiments/README.md`](experiments/README.md) covers the isolated
`qualify_libdeflate.py`, `libdeflate-1.26.json`, and `zlib-rs-0.6.7.json`
qualification inputs; none changes the default backend.
