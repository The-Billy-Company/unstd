---
doc_radar:
  sentinels:
    - file: src/unstd/compress/gzipx.py
      contains: ['GzipFile = _stdlib.GzipFile', 'if type(data) is not bytes']
    - file: src/unstd/compress/engines/reader.py
      contains: ['_factory = _accelerated.Inflater', '_zlib_globals["crc32"] = _crc32']
    - file: native/compress/src/adapter.c
      contains: ['libdeflate_gzip_decompress_ex(', 'MAX_OUTPUT_BYTES']
---

# `unstd.compress`

The public `gzipx.py` import stays at the package root, beside its grouped
[`engines/`](engines/README.md) concern. This is the restructure rule's
language-package-root shelf: the public import and native ABI stay stable while
implementation ownership has a descriptive home.

| Location | Responsibility |
| --- | --- |
| `gzipx.py` | Public gzip operations, backend selection and fallbacks. |
| [`engines/`](engines/README.md) | Native provider admission and bounded gzip reading. |
| `_gzip_native` binary and `.pyi` | Built native implementation and its type surface. |

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

The five public names are `open`, `GzipFile`, `compress`, `decompress`, and
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

With the compression extra and compiled read extension, streamed reads use a
native C inflater and libdeflate CRC32. The inflater links platform `libz` on
macOS and bundled zlib elsewhere. The running interpreter still supplies gzip
framing, buffering, header/trailer checks, concatenated members and seeking.
Small reads keep a bounded output budget rather than expanding the whole member.
Bytes delivered before malformed-stream failures retain the interpreter's order.

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
shape. Threaded streams, raw zlib, automatic format detection and archive
extraction are outside this interface.
