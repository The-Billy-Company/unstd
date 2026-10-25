# Gzip adapter and inflater

The native C sources build into `unstd.compress._gzip_native`:

| File | Entry points and state |
| --- | --- |
| [`adapter.c`](adapter.c) | `PyInit__gzip_native`, `compress`, `decompress`, `crc32`; per-call encoders and module-owned small decoder |
| [`fast.c`](fast.c), [`fast.h`](fast.h) | Unchanged upstream compressor instantiated with the owned three-candidate HT matchfinder |
| [`stream.c`](stream.c) | `Inflater` methods/properties and `unstd_add_inflater`; owns codec handle, input, offset, and EOF flags |
| [`inflate.c`](inflate.c) | `unstd_inflate_create/decode/free/version`; isolates the zlib ABI and allocator callbacks |
| [`module.h`](module.h) | Module-owned codec binding and decoder-lifetime declarations |
| [`shape.h`](shape.h) | Allocation-free first-block DEFLATE shape hint; never validates or consumes payload |
| [`isal_helpers.c`](isal_helpers.c) | Private CRC32, Adler32, and header initialization support for the Linux x86_64 ISA-L build |
| [`isal_patch.json`](isal_patch.json) | Exact bounded-read and error-acceptance edits applied only to verified temporary ISA-L sources |
| [`stream.h`](stream.h) | Shared declarations; keep signatures synchronized with both callers and definitions |

The buffer path treats ISIZE as untrusted: allocation is capped at 64 MiB and
by compressed-size expansion. Success requires a complete validated member and
only zero padding afterward; concatenated members and refused inputs return
`None` for stdlib replay. Outputs below 64 KiB reuse the module decoder with the
GIL held; larger outputs use a per-call decoder and release the GIL to decode.
Exact immutable input and the output reference remain live through that release.

The encoder is a whole-buffer path for exact `bytes` and exact integer levels
6 or 9; `gzipx.py` additionally requires integer `mtime=0`. It refuses inputs
below 256 bytes and uses at most the first 2048 bytes to reject concentrated
byte distributions, fewer than 24 distinct bytes, and exact periods 1–32.
Level 6 also requires all-ASCII input, a JSON object/array-shaped prefix after
horizontal whitespace, and no CR/LF in the sampled prefix; this is a shape hint,
not JSON validation. Public level 6 uses native level 1, whose HT finder checks
three inline candidates; public level 9 uses native level 6 and keeps upstream
HC/lazy matching. Every call owns its encoder and output bound, with Python RAW
allocation and GIL release for inputs of at least 64 KiB. Refused inputs return
`None` before compression; allocation and compression failures propagate.
Stateful writers and compressor copies do not enter this path.

Streaming retains exact `bytes`, snapshots other buffer exporters, and rechecks
state after exporter callbacks. Each object serializes its codec and properties
with a lock. Calls with at least 32 KiB of output space release the GIL only
around native inflation; buffer/index callbacks run before locking and error
imports run after unlocking. It respects `max_length`;
negative limits, and zero in legacy mode, mean unlimited. `needs_input`,
`unused_data`, and legacy `unconsumed_tail` must match the reader mode selected
by [`engines/reader.py`](../../../src/unstd/compress/engines/reader.py). Preserve these state
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

On macOS arm64, a configured zlib-ng 2.x provider can handle a complete small
final dynamic block dominated by length-258 matches at a constant distance of
at most 16 bytes. The read-only structural probe is capped at 8 KiB of held
compressed input and 4096 tokens. It requires a real end-of-block, valid trees
and distances, at most 32 literals and eight other matches, and at least 90%
of decoded bytes from the long matches. It produces no output and changes no
decoder, source position or read budget. Configuration uses
`RTLD_NOLOAD` on the already imported provider, verifies the native ABI, and
retains its handle in module state and independently in every live native
decoder. The immutable heap `Inflater` type participates in cycle collection.
A decoder selects only before its first
nonempty native call, with at least 16 KiB output space; missing symbols, ABI or
an inconclusive or incomplete probe retain libz. Initialization OOM propagates before either
engine decodes input and preserves retry state. A selected engine never falls
back after decoding. This is a performance hint, not an acceptance or allocation
rule. Other platforms and the explicit Rust archive retain their original ABI.

The optional Linux x86_64 ISA-L 2.31.1 build uses local NASM >=2.11.01 or
Yasm >=1.3.0 and keeps upstream files under `vendor/isa-l/` unchanged. Its
manifest, owned support C, and patch bytes are pinned in `hatch_build.py`. The
verified sources are copied to a private build directory before the owned
bounded-read and error-acceptance patch is applied. The patch also pins each
original and resulting source hash; an absent or repeated
edit context fails the build. The assembly archive has four objects and hides
its symbols at link time; C units use hidden visibility while CPython retains
the explicit module initializer export. Decoder selection uses conservative CPU
and stream-shape admission before decoding. Other architectures need no
assembler and retain their existing routes.


The Linux bridge retains saved header bits until more input arrives. Its cold
zero-output symbol check uses retained history and processes at most the first
literal in a packed lookup, so distance validation and error delivery respect
the caller's budget. Exhausted-input flags follow the reference provider even
when a symbol is internally pending.
