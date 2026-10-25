---
doc_radar:
  sentinels:
    - file: src/unstd/compress/engines/reader.py
      contains: ['class _Reader(', '_zlib_globals["crc32"] = _crc32']
    - file: src/unstd/compress/engines/imports.py
      contains: ['class _PortFinder(', 'def register_package(']
---

# Compression engines

The public codecs in the parent package own their APIs and fallback policy.
This leaf owns the boundaries to their compression engines: admitting native
providers, retaining the interpreter's gzip framing, checking flush arguments,
and preserving the identity of imported provider packages.

| File | Responsibility |
| --- | --- |
| `native.py` | Check the optional zlib-ng codec and gzip file interfaces. |
| `reader.py` | Bind native inflation and CRC to the interpreter's bounded gzip reader. |
| `flush.py` | Validate C integer flush modes at the provider's original callback boundary. |
| `imports.py` | Route qualified provider imports to their original module singletons. |

`gzipx` uses the native reader and deferred flush validation. `zlibx` shares the
flush-mode contract. Package ports request the import routing only when used;
importing `unstd.compress` does not load these engines or optional codecs.
Compiled code and upstream libraries live in
[`native/compress/`](../../../../native/compress/README.md); the extension and
its typing stub remain at `unstd.compress._gzip_native`.

The ISA-L reader distinguishes an empty buffered drain from an actual empty
source read before raising a truncated-member error. It keeps the interpreter's
member framing and input budgets, including source failure order and returned
bytes. Other inflater routes retain the interpreter's cloned reader code.

From the repository root, run:

```console
uv run --no-sync python -m pytest tests/suites/product/compress -q
```
