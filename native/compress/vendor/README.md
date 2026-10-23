# Authenticated gzip dependencies

[`libdeflate/`](libdeflate/) contains the selected gzip decoding and CRC32 source
closure, its [license](libdeflate/COPYING), and its
[authenticated manifest](libdeflate/UPSTREAM.json). [`zlib/`](zlib/) contains the
portable inflation closure, its [license](zlib/LICENSE), and its
[authenticated manifest](zlib/UPSTREAM.json).

These upstream trees are unmodified. Their directory structure and per-file
hashes are preserved. Release and supply-chain tools verify the complete source
closure and retain both licenses in built distributions. Project-owned adapter
code lives in [`../src/`](../src/README.md).
