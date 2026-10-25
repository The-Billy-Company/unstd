---
doc_radar:
  sentinels:
    - file: src/unstd/compress/gzipx.py
      contains: ['GzipFile = _stdlib.GzipFile', 'if type(data) is not bytes']
    - file: src/unstd/compress/_reader.py
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

With the compression extra, Linux streams use zlib-ng's native SIMD inflater;
macOS keeps its faster platform `libz` through the bundled C adapter. Other
platforms retain the bundled inflater. The compiled extension supplies
libdeflate CRC32 on all three paths. The running interpreter still supplies gzip
framing, buffering, header/trailer checks, concatenated members and seeking.
Small reads keep a bounded output budget rather than expanding the whole member.
Bytes delivered before malformed-stream failures retain the interpreter's order.
Independent streams can inflate concurrently. The bundled adapter releases the
GIL when a native call has at least 32 KiB of output space and serializes each
decoder's state with its own lock. Public `readinto` retains its existing staged
copy and error behavior; no expanded-output cache is retained across rewinds.

Eligible complete single-member exact `bytes` inputs use libdeflate for one-shot
`decompress`. The native kernel validates framing, CRC32, original size and
consumed input before returning output, with bounded speculative allocation.
Concatenations, unusual buffers, oversized candidates and inputs requiring
stdlib's permissive parsing or diagnostics use `gzip.decompress`. Bytearray,
view and subclass inputs retain stdlib behavior through that fallback. Streamed
reads do not replay a failed decode.

Writes use [python-zlib-ng](https://python-zlib-ng.readthedocs.io/en/stable/).
If a source build lacks the compiled read extension, streams use the zlib-ng
inflater and CRC32; one-shot decompression uses stdlib. Without the compression
extra, all five names remain exact stdlib bindings. Exceptions retain stdlib
families, including the original `BadGzipFile` class.

The [paired read measurements](../../../bench/reports/compress/gzip-native-reads-results.json)
record workload outputs, observed native calls, source identities and raw timing
samples. Throughput varies with platform, payload, compression level and read
shape. Automatic format detection, parallel framing of a single stream and
archive extraction are outside the gzip interface.

The [Billy qualification](../../../bench/reports/compress/billy-throughput.md)
measures the frozen dataset, two-pass upload, bounded HTTP and journal workloads
on Mac and Foundry Linux. Four independent upload streams improved 1.83× and
4.58× respectively; these are paired workload results. The
[port matrix](../../../bench/reports/compress/port-contracts.md) records external
codec coverage and the precise compatibility boundaries.

## Accelerated zlib wire operations

`from unstd.compress import zlibx as zlib` adds zlib-ng complete-buffer codecs,
incremental compression and native checksums behind the same `compress` extra.
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
