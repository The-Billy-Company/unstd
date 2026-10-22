# `unstd.compress`

Gzip through a stdlib-compatible surface, with zlib-ng compression behind
`unstd[compress]`. A base install uses stdlib `gzip` directly.

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

Read streams use the running interpreter's complete stdlib reader, including
bounded reads, seeking, header/trailer validation, CRC and concatenated members.
The native inflater and compatibility adapter slowed common JSON reads; installing
the compression extra now retains the stdlib read path. One-shot decompression
uses stdlib zlib's C gzip parser for a complete single member, including optional
zero padding. Concatenated members, incomplete input and codec failures fall back
to `gzip.decompress` for its exact parsing rules and diagnostics. Streamed reads
are never replayed. Native write errors become stdlib `zlib.error`;
`BadGzipFile` is the stdlib class.

We use [python-zlib-ng](https://python-zlib-ng.readthedocs.io/en/stable/) for its
native DEFLATE compressor. We retain [Python's gzip contract](https://docs.python.org/3/library/gzip.html)
around that codec. Throughput and compression size must both be measured for
the caller's payload and explicit compression level. Threaded streams, raw zlib,
automatic format detection, and archive extraction are outside this interface.
