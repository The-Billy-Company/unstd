# Gzip adapter and inflater

All three C files build into `unstd.compress._gzip_native`:

| File | Entry points and state |
| --- | --- |
| [`adapter.c`](adapter.c) | `PyInit__gzip_native`, `decompress`, `crc32`; module-owned small libdeflate decoder |
| [`stream.c`](stream.c) | `Inflater` methods/properties and `unstd_add_inflater`; owns codec handle, input, offset, and EOF flags |
| [`inflate.c`](inflate.c) | `unstd_inflate_create/decode/free/version`; isolates the zlib ABI and allocator callbacks |
| [`module.h`](module.h) | Module-owned codec binding and decoder-lifetime declarations |
| [`shape.h`](shape.h) | Allocation-free first-block DEFLATE shape hint; never validates or consumes payload |
| [`stream.h`](stream.h) | Shared declarations; keep signatures synchronized with both callers and definitions |

The buffer path treats ISIZE as untrusted: allocation is capped at 64 MiB and
by compressed-size expansion. Success requires a complete validated member and
only zero padding afterward; concatenated members and refused inputs return
`None` for stdlib replay. Outputs below 64 KiB reuse the module decoder with the
GIL held; larger outputs use a per-call decoder and release the GIL to decode.
Exact immutable input and the output reference remain live through that release.

Streaming retains exact `bytes`, snapshots other buffer exporters, and rechecks
state after exporter callbacks. Each object serializes its codec and properties
with a lock. Calls with at least 32 KiB of output space release the GIL only
around native inflation; buffer/index callbacks run before locking and error
imports run after unlocking. It respects `max_length`;
negative limits, and zero in legacy mode, mean unlimited. `needs_input`,
`unused_data`, and legacy `unconsumed_tail` must match the reader mode selected
by [`_reader.py`](../../../src/unstd/compress/_reader.py). Preserve these state
transitions when changing buffering, especially at EOF and bounded reads.

The private `decompress_into` primitive returns an owning view of a writable,
contiguous target. It shares the same decoder loop and may write a prefix before
a codec error. The public gzip reader retains staged output and does not expose
that mutation boundary to caller-provided `readinto` buffers.

`inflate.c` bounds allocator multiplication and uses Python RAW allocators.
Input/output pointers are borrowed per decode call and cleared afterward. It
links macOS `libz`, the prefixed [vendored zlib](../vendor/README.md) elsewhere,
or an explicitly selected [local zlib-rs archive](../experiments/README.md).
Allocation errors propagate; decode failures become `zlib.error` in `stream.c`.

On macOS arm64, a configured zlib-ng 2.x provider can handle a narrow first-block
shape with short distances and a dominant long-match code. Configuration uses
`RTLD_NOLOAD` on the already imported provider, verifies the native ABI, and
retains its handle in module state. The immutable heap `Inflater` type retains
that module for every live decoder. A decoder selects only before its first
nonempty native call, with at least 4 KiB output space; missing symbols, ABI or
an inconclusive header retain libz. Initialization OOM propagates before either
engine decodes input and preserves retry state. A selected engine never falls
back after decoding. This is a performance hint, not an acceptance or allocation
rule. Other platforms and the explicit Rust archive retain their original ABI.
