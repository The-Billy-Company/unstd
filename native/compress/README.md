# Native gzip reads

`adapter.c` decodes eligible immutable gzip buffers with libdeflate. The native
kernel validates the gzip frame, CRC32, original size and consumed input before
returning bytes. It returns `None` for unsupported inputs or failed validation so
the Python wrapper can preserve its existing streaming and diagnostic behavior.
The last four input bytes provide an allocation hint; an absolute 64 MiB limit
and a compressed-size expansion budget bound speculative allocation.

[`libdeflate/`](libdeflate/) contains the unmodified source closure for gzip
decompression from [libdeflate 1.25](https://github.com/ebiggers/libdeflate/tree/v1.25).
Its [MIT license](libdeflate/COPYING) is included. Compression kernels, zlib
framing, CLI programs and build tooling are omitted. The archive URL, SHA256 and
individual source hashes are recorded in [UPSTREAM.json](libdeflate/UPSTREAM.json).
The ordinary extension build compiles these C sources directly; no system
libdeflate library, download or separate build tool is required.

`stream.c` retains compressed input and inflates directly into a Python byte
buffer within the caller's output budget. `inflate.c` calls the native zlib C
ABI; it makes no Python stdlib compression calls. macOS links its platform
`libz`, whose ARM64 inflater was faster on Billy's measured workloads. Other
platforms compile the unmodified [zlib 1.3.2](https://zlib.net/) inflation source
closure in [`zlib/`](zlib/) with `Z_PREFIX` to isolate its symbols. The
[license](zlib/LICENSE), archive checksum and individual source hashes are
included in [UPSTREAM.json](zlib/UPSTREAM.json). No Rust toolchain is required.

The Python reader retains its interpreter's gzip framing, buffering and error
delivery while selecting the compiled inflater and libdeflate CRC32 function.
