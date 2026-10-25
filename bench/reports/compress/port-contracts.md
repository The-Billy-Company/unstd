# Compression port contracts

This is an API and behavior inventory, with no throughput claim. It maps the
external Python codec calls inspected in the consumer on 2026-10-25 to `unstd` routes.
Source paths below are relative to the consumer repository. The inspected checkout's HEAD
was `b913877f51c711679a4a405ca152cca9a9f0f95a`; key source hashes appear below.
No consumer source was changed for this inventory.

## Route and guarantee matrix

An **identity port** returns the original module object, including its functions,
classes, exceptions and state objects. Within the same runtime, changing only
that import changes no codec behavior. An **accelerated route** preserves the
stated interface and recovered bytes, while its codec implementation can differ.
Neither term promises identical behavior across different Python/provider/native
library versions or identical speed on different machines.

| External surface | Import through `unstd.compress` | Guarantee and boundary |
| --- | --- | --- |
| `gzip` | `gzipx as gzip` | Accelerated `open`, `GzipFile`, `compress`, `decompress`; original `BadGzipFile`. Interpreter defaults, gzip framing, concatenated members, buffered delivery, seek/tell, text wrapping and file ownership are retained. Compressed bytes may change. Without `compress` dependencies, all five public names are exact stdlib bindings. |
| `zlib` | `zlib` | Identity port, including private/native factories present in that interpreter. Use for stock compressed bytes, complete API identity, and interpreter-specific state behavior. |
| `zlib` wire operations | `zlibx as zlib` | With `unstd[compress]`, zlib-ng complete-buffer compression/decompression, compressor state/copy/dictionaries and CRC32/Adler32; errors normalize to stdlib `zlib.error`. `decompressobj` remains the exact stdlib factory. New, unqualified interpreter APIs retain stdlib bindings. Compressed bytes and provider version strings may change. |
| `bz2`, `lzma` | `bz2`, `lzma` | Identity ports: formats, filters, presets, bounded decoder state, errors, buffers and file ownership remain the interpreter's. No replacement native library or interpreter patch is installed. |
| `zstandard` | `zstandard` | Identity port of python-zstandard, requiring `unstd[zstd]`. Native contexts, dictionaries, frame parameters, stream defaults and `ZstdError` are unchanged. Provider limits remain in force. |
| `compression.zstd` | `zstd` | Identity port of Python 3.14+ `compression.zstd`, including `open` and text streams. Explicit import fails on older Python; it does not substitute python-zstandard. |
| `zipfile`, `tarfile` | `zipfile`, `tarfile` | Identity ports: original archive classes, metadata, exceptions, streaming modes, extraction/filter options and internal codec selection. Importing them does not redirect their internal gzip/zlib calls to accelerated routes. |
| `libarchive`, `libarchive.ffi/read/exception/...` | `libarchive`, `libarchive.ffi/read/exception/...` | Identity ports of libarchive-c and its qualified child modules, requiring `unstd[archives]` and a loadable native libarchive. Existing ctypes callbacks, warning/error handling and native diagnostic state remain shared. Format/filter support depends on the host library. |

The namespaces are lazy: importing `unstd.compress` or calling `dir()` loads no
optional codec. Missing optional providers fail when their namespace is requested.
The `archives` extra supplies its binding on Linux/macOS; Windows requires a
compatible binding and native library supplied separately.

## Observed consumer operations

| Consumer source and symbol | External operations and corresponding route |
| --- | --- |
| `services/ai/tools/base/datasets.py`: `query_dataset`, `materialize_datasets` | Already imports `gzipx as gzip`: decode `BytesIO` streams into `ijson` batches or `sandbox_upload_from`; encode result rows with `gzip.compress`. Existing `zlib.error` catches remain valid. |
| `services/ai/tools/code/sandbox/client/channel.py`: `sandbox_upload_from` | Codec-agnostic source contract: save `tell()`, rewind, hash/count in 256 KiB reads, rewind for streamed transfer, restore position. `gzipx.GzipFile` supports that contract without retaining a decoded-output cache across rewinds. |
| `services/ai/engine/runtime/journal/buffer.py`: `_open_file`, `_flush_batch`; `journal/replay.py`: `load_journal` | Already uses `gzipx`: append binary files, write one encoded entry at a time, flush batches, close/reopen into later members, iterate and parse lines on replay. A flush retains the current member; reopening append creates another member. |
| `services/ai/core/foundation/io/net/body.py`: `_decoded`; `core/domain/knowledge/reading/mime.py`: `native_mime` | Gzip `read(max_bytes + 1)` uses `gzipx`. Bounded zlib/raw/gzip `decompressobj` operations, wrapper fallback, `eof`, `unused_data` and `unconsumed_tail` use `zlib` or `zlibx`'s exact stdlib decoder. HTTP allowance charging, completeness checks, refusal and MIME policy remain with the application. |
| `services/ai/core/foundation/util/text/compress.py`: `gzip_encode`, `gzip_decode`, `_GzipPrefix.encode` | `compress`/`decompress(wbits=31)`, compressor copy, 64 KiB prefix checkpoint, memoryview suffix and final flush can use `zlibx`; `zlib` retains stock compression. `gzip_request`, `gzip_request_scope` and the prefix lifetime/locking policy remain with the application. |
| `scripts/act/delivery/ship/hosting/downloads.py`: `_stock`, `_matches`, `preserve` | **Keep the identity `zlib` route** for level-9 raw DEFLATE, custom gzip header/trailer, incremental CRC and comparison against the published compressed SHA256. `gzipx`/`zlibx` make no stock compressed-byte promise. Publisher OS-byte normalization, hash authentication and Node/host-gzip alternatives remain with the application. |
| `services/ai/core/foundation/util/text/compress.py`: `_Contexts`, `zstd_encode`, `zstd_decode`, `wire_compress_for`, `wire_decompress`; `core/platform/jobs/signalbus/adapters/jetstream.py` | Use `zstandard`: native context construction, dictionary IDs, frame content/window sizes, complete-buffer decode options, incremental `eof`/`unused_data`, and `ZstdError`. Thread-local reuse, dictionary dispatch, magic byte, emit-if-smaller rule, concatenated-frame loop and whole-wire passthrough on failure remain with the application. |
| `scripts/observe/telemetry/wire_entropy/dict_train.py`: `train_all`; `dict_eval.py`: `_train`, `_verify_roundtrip`; `services/ai/evals/suites/agent/artifacts/creative/photography.py`: `_bundle` | `zstandard` preserves `train_dictionary`, `ZstdCompressionDict.dict_id()/as_bytes()`, dictionary codecs and `stream_reader(BytesIO(...)).read()` with its original defaults. The last operation reads complete output and is not an added bounded-decode API. |
| `tools/igor/connectors/telemetry/answers/artifacts.py`: `load`, `save` | `zstd` preserves stdlib `.zst` text `open`, UTF-8 reads/writes, `newline=""`, and ownership of a supplied binary stream. JSON framing, private-file policy, fsync and publication remain Igor's. |
| `services/ai/core/foundation/io/net/tracking.py`: `hosts`, `header`, `refresh`; `scripts/observe/telemetry/entropy/baselines.py` | `lzma` preserves UTF-8 text opens, preset `9 | PRESET_EXTREME`, and raw `FORMAT_RAW`/`FILTER_LZMA2` encoding. `infra/docker/dockerfiles/sandbox/blender_security_proof.py` also exercises original bz2/lzma/zlib native error and allocation-failure state; identity ports retain that installed interpreter's behavior and patches. |
| `services/ai/tools/code/media/blender/authoring/creation.py`: `create`; `tools/code/sandbox/contents/snapshot.py`: `_restore`; `core/domain/knowledge/reading/opendocument.py`: `_package` | `zipfile` preserves `ZipFile`, `ZipInfo`, compression constants/levels, member metadata, reads/writes and `BadZipFile`/`is_zipfile`. Package-specific validation, duplicate/path checks and extraction policy remain with the application. |
| `scripts/act/delivery/ship/push/context.py`: `extract`; `scripts/act/workspace/archive/attic.py`: `_StreamingTar`, `assert_safe_archive`, `probe_archive` | `tarfile` preserves `r|` iteration, `TarFile` subclassing and `stream=True` header-cache behavior where the runtime supports it. Manifest/hash checks, path/link validation, subprocess lifecycle and extraction remain with the application. |
| `services/ai/core/foundation/util/archives.py`: `members`, `Budget`; `core/domain/knowledge/reading/metadata.py`: `_archive` | `libarchive` preserves raw-format registration, memory opens, archive/entry iteration, bounded `get_blocks`, metadata, filter names, `ArchiveError` and `ffi.error_string`. Shared cumulative byte/entry/depth budgets, names/links/encryption policy and promotion of warnings to failures remain with the application. Header-only metadata inspection does not establish payload integrity. |

## Behavior, state and memory boundaries

- **Gzip streaming:** reads use the running interpreter's framing bytecode and
  buffering, with the selected native inflater and CRC kernel. Linux selects
  zlib-ng; macOS retains platform libz through the bundled adapter. Unsupported
  native builds retain the portable zlib-ng route. Member CRC/length checks and
  the bytes delivered before later malformed-member failures retain stdlib's
  ordering. Native codec errors become the original stdlib exception family;
  byte-for-byte diagnostic strings are not a cross-provider guarantee.
- **Ownership and callbacks:** explicit gzip file objects remain caller-owned;
  owned paths close on normal exit and tested setup/read/write failures. Source
  I/O exceptions propagate unchanged. Public `readinto` retains staged-copy
  behavior on codec errors. The private native `decompress_into` can mutate its
  target before failing; it is not wired into public caller-buffer reads.
  Native argument/export callbacks run before decoder locking, and error
  translation runs after unlocking. Stateful codec failures are never replayed
  through another decoder.
- **Native state:** bundled inflaters serialize their own state and release the
  GIL for native calls with at least 32 KiB of output space. This permits
  independent streams to progress concurrently; it does not make a shared
  `GzipFile`, compressor or optional-provider context an application transaction.
  Identity ports retain their provider's threading and lifetime rules. No
  application-owned zstd contexts or decoded results are shared or cached by
  these ports.
- **Limits:** a bounded gzip read limits returned bytes and uses incremental
  inflation with the interpreter's existing buffering; it is not a total-memory
  cap equal to the requested length. `zlib.decompress(..., bufsize=...)` is an
  initial allocation hint, not an output limit; `decompressobj(...).decompress`
  with `max_length=0` is unlimited. Native python-zstandard's `max_output_size`
  is not a strict budget for frames with a known content size. The consumer
  `zstd_decode` therefore also checks declared content size and window size.
  Stream reads, decoder windows, dictionaries, retained input and application
  accumulation have their original separate costs.
- **One-shot gzip:** exact `bytes` can use libdeflate only after validating a
  complete single member, CRC, original size and consumed input. Speculative
  native output is limited to 64 MiB and a compressed-size expansion bound;
  this is a fast-path eligibility limit, **not** a public decompression limit.
  Other buffers, concatenations, oversized candidates and permissive parsing or
  diagnostics fall back to stdlib. Input subclasses retain stdlib callbacks.
- **Byte identity:** recovered bytes and exact checksum integers are the
  interchange contract for accelerated routes. Fixed timestamps make a given
  writer deterministic but do not guarantee stock DEFLATE bytes or identical
  gzip headers across codecs. Identity ports preserve the original call within
  the installed runtime; matching persisted compressed hashes across native
  versions still needs the original encoder/build and application authentication.

## Excluded owners

`core/foundation/io/grpc.py` and `transport/rpc/server/bootstrap/bootstrap.py`
select `grpc.Compression.Gzip`; gRPC owns that native transport codec. Imports
through `unstd.compress` do not replace it. Likewise, `attic.py`'s external
`xz`/`zstd` pipelines, `hosting/downloads.py`'s Node/host `gzip`, and
`evals/suites/substrate/snapshots/replay.py::_tolerant_load`'s `gzip -cdf`
recovery retain their own process, format and failure protocols. Go/native
services and browser `DecompressionStream` also remain outside these Python
routes. This port does not move application wire framing, application budgets,
archive policy, recovery policy or publication/authentication into `unstd`.

## Verification and source evidence

The relevant checked contracts are in
[`test_module_ports.py`](../../../tests/suites/product/compress/unit/test_module_ports.py)
(module identity, zstd dictionaries/frame limits and ownership, bz2/lzma bounded
state, stock zlib bytes, ZIP metadata),
[`test_libarchive_port.py`](../../../tests/suites/product/compress/unit/test_libarchive_port.py)
(child identity, native blocks/diagnostics and unchanged callbacks),
[`test_zlibx.py`](../../../tests/suites/product/compress/unit/test_zlibx.py)
(copy, dictionaries, scalar/buffer callbacks, EOF/flush/error state and no replay),
[`test_native_reader.py`](../../../tests/suites/product/compress/unit/test_native_reader.py)
(native execution, bounded reads and failure-delivery histories),
[`test_native_threads.py`](../../../tests/suites/product/compress/unit/test_native_threads.py)
(serialization, reinitialization, GIL progress and reentrant error translation),
and [`test_stream_faults.py`](../../../tests/suites/product/compress/integration/test_stream_faults.py)
(I/O faults and owned/borrowed file cleanup).
[`test_gzipx.py`](../../../tests/suites/product/compress/unit/test_gzipx.py),
[`test_gzipx_edges.py`](../../../tests/suites/product/compress/unit/test_gzipx_edges.py),
[`test_rfc1952_layout.py`](../../../tests/suites/product/compress/unit/test_rfc1952_layout.py)
and [`test_native_buffers.py`](../../../tests/suites/product/compress/unit/test_native_buffers.py)
cover defaults, framing, callbacks, one-shot validation/fallback and byte delivery.
The gzip consumer profiler in the [profile directory](../../profiles/) records the ten source-consumer
symbols and hashes for its dataset, upload, bounded HTTP and journal fixtures;
those fixtures validate the isolated codec/I/O portion, excluding RPC scheduling
and filesystem latency.

The routing implementation is in
[`gzipx.py`](../../../src/unstd/compress/gzipx.py),
[`engines/reader.py`](../../../src/unstd/compress/engines/reader.py),
[`zlibx.py`](../../../src/unstd/compress/zlibx.py), and the module-alias files in
[`src/unstd/compress`](../../../src/unstd/compress).
With the inspected python-zstandard 0.25.0 C backend, a declared 4,096-byte
frame returned all 4,096 bytes with `max_output_size=1`. Its accompanying
`backend_cffi.py`, `ZstdDecompressor.decompress`, also allocates the declared
size independently of that option for known-size frames.

SHA256 receipts for the source decisions that cross API boundaries:

```text
bc1a2aaf072e7cbe731b2057158a45e2b46abb2a3981fa5a32acd7b768f2c29a  services/ai/core/foundation/util/text/compress.py
b05a688a08341191005c980b8820ef65fdcf8c9376189d6536d5dff33d81ffeb  services/ai/core/foundation/util/archives.py
3a5afc84bbf7f1715bbb94a7f22e5ea834a662a2a0d16425f658cda7532af4a3  services/ai/core/foundation/io/net/body.py
4d778a144e10e78fc74bf88aac571642ce04e869f33bbb537dcaa9d9e0f24a94  services/ai/core/domain/knowledge/reading/mime.py
23fc4dc071313fcb4225dc612ab8d4adbca52636ab6d611e968800f6d269b280  scripts/act/delivery/ship/hosting/downloads.py
a1f381f76273e9e178aea42caa86612a93998f1faa5181c274918c0df7d8457b  tools/igor/connectors/telemetry/answers/artifacts.py
5384805fe0f97a026a213e5ad7ccf4fc28869bbf28044ff3e419ef5721f225df  scripts/act/workspace/archive/attic.py
```
