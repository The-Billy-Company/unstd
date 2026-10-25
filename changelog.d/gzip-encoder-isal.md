Eligible whole-buffer `gzipx.compress` calls use the bundled native encoder for
exact bytes, public levels 6 or 9, and integer `mtime=0`. Conservative input
guards retain the existing provider for unsuitable payloads. Level 6 maps to
native level 1 with an owned three-candidate hash-table matchfinder; level 9 maps
to native level 6. Compressed bytes can change while the recovered payload
remains the contract. Stream writers and compressor copies keep their existing
providers, and native failures are not replayed.

Linux x86_64 builds can include reviewed ISA-L 2.31.1 source using a local NASM
or Yasm assembler. Verified vendored sources are copied and patched only in the
private build directory. Supported CPUs and admitted stream shapes can select
ISA-L before decoding; other builds and streams retain their existing routes.
No mandatory runtime dependency or universal speedup is claimed.
