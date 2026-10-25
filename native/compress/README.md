# Native gzip reads

Private build inputs for `unstd.compress._gzip_native`; callers use
[`unstd.compress.gzipx`](../../src/unstd/compress/README.md).

[`src/`](src/README.md) implements whole-buffer gzip decoding, CRC32 and bounded
streaming inflation. The buffer path accepts exact `bytes`, treats ISIZE as an
untrusted allocation hint, and returns only after frame, checksum, size and input
consumption checks. Refusal returns `None` for stdlib replay; allocation failures
propagate. Streaming framing and error delivery stay in the running interpreter's
gzip reader, selected by [`_reader.py`](../../src/unstd/compress/_reader.py).

[`hatch_build.py`](../../hatch_build.py) compiles vendored libdeflate 1.25 for
buffer decoding and CRC32. Streaming uses platform `libz` on macOS and vendored
zlib 1.3.2 with `Z_PREFIX` elsewhere. The [vendor manifests](vendor/README.md)
record the exact source closures and licenses; default builds need no Cargo,
source downloads or system libdeflate.

[`experiments/`](experiments/README.md) documents isolated libdeflate 1.26 and
zlib-rs qualification. Neither changes the default backend.
