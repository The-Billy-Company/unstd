---
doc_radar:
  sentinels:
    - file: src/unstd/compress/engines/reader.py
      contains: ['class _Reader(', '_zlib_globals["crc32"] = _crc32']
---

# Compression engines

The parent `gzipx.py` module owns the public API and fallback policy. This leaf
owns native provider admission and binds native inflation and CRC to the running
interpreter's bounded gzip framing.

| File | Responsibility |
| --- | --- |
| `native.py` | Check the optional zlib-ng codec and gzip file interfaces. |
| `reader.py` | Bind native inflation and CRC to the interpreter's bounded gzip reader. |

Compiled code and authenticated upstream libraries live in
[`native/compress/`](../../../../native/compress/README.md). The binary and
its typing stub retain the `unstd.compress._gzip_native` import path.

From the repository root, run:

```console
uv run --no-sync python -m pytest tests/suites/product/compress -q
```
