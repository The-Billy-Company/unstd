---
doc_radar:
  sentinels:
    - file: src/unstd/compress/gzipx.py
      contains: ['GzipFile = _stdlib.GzipFile', 'if type(data) is not bytes']
    - file: src/unstd/compress/engines/reader.py
      contains: ['_factory = _accelerated.Inflater', '_zlib_globals["crc32"] = _crc32']
    - file: native/compress/src/adapter.c
      contains: ['libdeflate_gzip_decompress_ex(', 'MAX_OUTPUT_BYTES']
    - file: src/unstd/compress/__init__.py
      contains: ['def __getattr__(', 'def __dir__(']
    - file: src/unstd/compress/zstandard.py
      contains: ['sys.modules[__name__] = _backend', "unstd[zstd]"]
    - file: src/unstd/compress/libarchive/__init__.py
      contains: ['_sys.modules[__name__] = _backend', "unstd[archives]"]
---

# `unstd.compress`

The public import shelf stays at this package root: `gzipx` and `zlibx` own
accelerated codecs, and the format-named ports expose original modules. Their
filenames are consumer import paths and pair with typing stubs. This is the
language-package-root case in the restructure rules; turning every small port
into a one-file package would add interfaces without adding ownership.

| Location | Responsibility |
| --- | --- |
| `gzipx.py`, `zlibx.py` | Public gzip and zlib operations, backend selection and fallbacks. |
| `bz2.py`, `lzma.py`, `zlib.py` | Original interpreter codecs. |
| `tarfile.py`, `zipfile.py` | Original interpreter archive APIs. |
| `zstd.py`, `zstandard.py` | Original stdlib and optional Zstandard providers. |
| [`engines/`](engines/README.md) | Native provider admission, bounded reading, flush validation and import identity. |
| [`libarchive/`](libarchive/README.md) | Original libarchive binding and its qualified typing surface. |
| `*.pyi`, `_gzip_native` binary | Type information and the built private extension. |

Gzip through a stdlib-compatible surface, with native codecs behind
`unstd[compress]`. A base install binds the five public names directly to stdlib
`gzip`.

```python
from unstd.compress import gzipx
from unstd.serde import ndjson

raw = gzipx.compress(b"payload", mtime=0)
assert gzipx.decompress(raw) == b"payload"

with gzipx.open("events.jsonl.gz", "wb") as fp:
    ndjson.dump([{"event": "saved"}], fp)

with gzipx.open("events.jsonl.gz", "rb") as fp:
    for row in ndjson.iter_loads(fp):
        ...
```

The five public `gzipx` names are `open`, `GzipFile`, `compress`, `decompress`, and
`BadGzipFile`. Binary opens return `GzipFile`; text opens return `TextIOWrapper`.
Paths and existing file objects work, including append/exclusive modes, iteration,
seeking and text encodings. Closing a gzip wrapper leaves an explicitly supplied
file object open. `codec=structs.Codec(Event)` on the NDJSON calls adds typed
streaming using the existing serde extra.

We preserve the running interpreter's defaults: compression level 9 through
Python 3.14, and one-shot `compress` timestamps defaulting to current time before
3.14 and zero from 3.14. Pass `mtime=0` explicitly for reproducible timestamps.
File writers retain `mtime=None`. Compressed bytes and ratios may differ between
engines; the interchange contract is gzip streams and identical recovered bytes.

With the compression extra, Linux normally uses zlib-ng's native SIMD inflater.
Linux x86_64 builds with the reviewed ISA-L codec and supported CPU can use the
bundled bridge, selecting ISA-L before decoding for admitted stream shapes and
retaining zlib-ng for other inputs. macOS keeps its faster platform `libz`
through the bundled C adapter. Other platforms retain the bundled inflater. The compiled extension supplies
libdeflate CRC32 on native paths. The running interpreter still supplies gzip
framing, buffering, header/trailer checks, concatenated members and seeking.
Small reads keep a bounded output budget rather than expanding the whole member.
Bytes delivered before malformed-stream failures retain the interpreter's order.
Independent streams can inflate concurrently. The bundled adapter releases the
GIL when a native call has at least 32 KiB of output space and serializes each
decoder's state with its own lock. Public `readinto` retains its existing staged
copy and error behavior; no expanded-output cache is retained across rewinds.
On Mac ARM, the C adapter can select the already loaded zlib-ng inflater for a
complete small DEFLATE block dominated by repeated short-distance matches.
The bounded probe selects before decoding and preserves source consumption,
output limits and the interpreter's framing. Incomplete probes keep Apple libz;
Linux keeps its zlib-ng SIMD reader unless its optional ISA-L route admits the
stream. Selection adds no package dependency.
The [single-stream qualification](../../../bench/reports/compress/single-stream/README.md)
records repetitive-stream gains, server parity, edge costs and streaming checks.
The [Linux and Mac C qualification](../../../bench/reports/compress/linux-throughput/README.md)
records server streaming gains and faster request and dataset encoding on both
machines. Linux release wheels include the private SIMD decoder; source builds
need local NASM or Yasm to enable it. No ISA-L Python package is needed at runtime.

Eligible complete single-member exact `bytes` inputs use libdeflate for one-shot
`decompress`. The native kernel validates framing, CRC32, original size and
consumed input before returning output, with bounded speculative allocation.
Concatenations, unusual buffers, oversized candidates and inputs requiring
stdlib's permissive parsing or diagnostics use `gzip.decompress`. Bytearray,
view and subclass inputs retain stdlib behavior through that fallback. Streamed
reads do not replay a failed decode.

Whole-buffer `compress` can use the private C encoder for exact `bytes` objects,
exact integer levels 6 or 9, and integer `mtime=0`. Conservative input guards
keep small, low-entropy and short-period inputs on the existing
[python-zlib-ng](https://python-zlib-ng.readthedocs.io/en/stable/) route. Level 6
also requires a compact ASCII JSON-shaped prefix and uses native level 1 with
the owned three-candidate hash-table matchfinder; level 9 uses native level 6.
Other levels, timestamps and buffer types retain the existing provider route.
Stream writers and compressor copies keep their existing behavior.

If a source build lacks the compiled extension, streams use the zlib-ng
inflater and CRC32; one-shot decompression uses stdlib. Without the compression
extra, all five names remain exact stdlib bindings. Exceptions retain stdlib
families, including the original `BadGzipFile` class.

The [paired read measurements](../../../bench/reports/compress/gzip-native-reads-results.json)
record workload outputs, observed native calls, source identities and raw timing
samples. Throughput varies with platform, payload, compression level and read
shape. Automatic format detection, parallel framing of a single stream and
archive extraction are outside the gzip interface.

The [consumer qualification](../../../bench/reports/compress/README.md)
measures the frozen dataset, two-pass upload, bounded HTTP and journal workloads
on Mac and the Linux server. Four independent upload streams improved 1.83× and
4.58× respectively; these are paired workload results. The
[port matrix](../../../bench/reports/compress/port-contracts.md) records external
codec coverage and the precise compatibility boundaries.

## Accelerated zlib wire operations

`from unstd.compress import zlibx as zlib` adds zlib-ng complete-buffer codecs,
incremental compression before Python 3.15, and native checksums behind the same
`compress` extra. On Python 3.15, `compressobj` is the exact stdlib factory:
zlib-ng 1.0 can abort the interpreter when copying a finished compressor, with
the fix currently listed in its
[development changelog](https://python-zlib-ng.readthedocs.io/en/latest/#changelog).
Complete-buffer codecs, checksums and gzip acceleration still use zlib-ng.
It preserves raw/zlib/gzip window modes, compressor copying, dictionaries and
stdlib exception families. Oversized flush modes fail before they can finish a
compressor. Incremental `decompressobj` is the running stdlib's C factory: this
preserves interpreter-specific EOF, unused-input, copy, flush and error-state
semantics that differ from the optional backend. Stateful calls are never
replayed after an error. A base install uses exact stdlib bindings.

Compressed bytes can differ under `zlibx`, as they can under `gzipx`. Keep the
original `zlib` namespace below when persisted hashes require stock compressed
bytes. The version strings in `zlibx` describe its accelerated provider;
`unstd.compress.zlib` retains the interpreter's original version strings.

## Original codec modules

We expose the original modules when a consumer needs their complete API:

```python
from unstd.compress import bz2, lzma, zlib, zipfile, tarfile
from unstd.compress.zipfile import ZipFile, ZipInfo
```

These are the running interpreter's modules themselves. Compressor copies,
raw/gzip window modes, incremental checksums, bounded decoder state, archive
metadata, exception classes and file ownership retain their stdlib behavior.
`zlib` keeps the stock compressor for consumers that compare compressed bytes.
Archive path validation, extraction filters and cumulative budgets stay with the
calling application.

For package providers, a private import finder routes qualified child imports
to their canonical modules. It is installed only when such a namespace is
requested and preserves original module metadata, classes and exceptions.
Optional Zstandard backends remain lazy; no codec operation passes through the
finder.

For the full native [python-zstandard](https://python-zstandard.readthedocs.io/)
API, install `unstd[zstd]`:

```python
from unstd.compress import zstandard
from unstd.compress.zstandard import ZstdCompressor, ZstdError
```

This is the original optional backend, including dictionaries, frame parameters,
stream readers/writers, output limits and native classes. Its own guarantees and
limits remain in force; callers still check declared frame size and window size
when their policy requires a strict allocation budget. We do not share or cache
caller-owned compression contexts.

`from unstd.compress import zstd` exposes Python 3.14's original
`compression.zstd`, including its text/file API. Earlier Python versions report
a clear `ImportError` when this namespace is requested. The stdlib and native
Zstandard APIs remain separate. All namespaces load on first request; importing
`unstd.compress` or listing its names loads no optional backend.

For the full external archive binding, `unstd[archives]` exposes the original
`libarchive-c` package and qualified modules such as
`unstd.compress.libarchive.ffi` and `unstd.compress.libarchive.read`. A loadable
native libarchive library is also required. Modules, error classes, callbacks,
format/filter capabilities and context ownership remain the provider's; archive
validation and resource policy stay with the application. See the
[native archive namespace](libarchive/README.md).
The `archives` extra installs its binding on Linux and macOS; Windows hosts must
provide a compatible binding and native library separately.
