# Gzip adapter and inflater

All three C files build into `unstd.compress._gzip_native`:

| File | Entry points and state |
| --- | --- |
| [`adapter.c`](adapter.c) | `PyInit__gzip_native`, `decompress`, `crc32`; module-owned small libdeflate decoder |
| [`stream.c`](stream.c) | `Inflater` methods/properties and `unstd_add_inflater`; owns codec handle, input, offset, and EOF flags |
| [`inflate.c`](inflate.c) | `unstd_inflate_create/decode/free/version`; isolates the zlib ABI and allocator callbacks |
| [`stream.h`](stream.h) | Shared declarations; keep signatures synchronized with both callers and definitions |

The buffer path treats ISIZE as untrusted: allocation is capped at 64 MiB and
by compressed-size expansion. Success requires a complete validated member and
only zero padding afterward; concatenated members and refused inputs return
`None` for stdlib replay. Outputs below 64 KiB reuse the module decoder with the
GIL held; larger outputs use a per-call decoder and release the GIL to decode.
Exact immutable input and the output reference remain live through that release.

Streaming retains exact `bytes`, snapshots other buffer exporters, and rechecks
state after exporter callbacks. It keeps the GIL and respects `max_length`;
negative limits, and zero in legacy mode, mean unlimited. `needs_input`,
`unused_data`, and legacy `unconsumed_tail` must match the reader mode selected
by [`_reader.py`](../../../src/unstd/compress/engines/reader.py). Preserve these state
transitions when changing buffering, especially at EOF and bounded reads.

`inflate.c` bounds allocator multiplication and uses Python RAW allocators.
Input/output pointers are borrowed per decode call and cleared afterward. It
links macOS `libz`, the prefixed [vendored zlib](../vendor/README.md) elsewhere,
or an explicitly selected [local zlib-rs archive](../experiments/README.md).
Allocation errors propagate; decode failures become `zlib.error` in `stream.c`.
