# Native JSON dispatch

Private `unstd.serde._json_native` extension for CPython GIL builds. It
requires `orjson`; decoding also needs `msgspec`. The Python
[`jsonx`](../../src/unstd/serde/jsonx.py) and
[`ndjson`](../../src/unstd/serde/ndjson.py) wrappers own compatibility and fallback.

| Source | Responsibility |
| --- | --- |
| [`src/adapter.c`](src/adapter.c) | Module state, backend binding, exact-builtin encoding proofs and JSON spacing |
| [`src/encode.h`](src/encode.h) | Guarded `dumpb` vectorcall binding on CPython 3.14 |
| [`src/decode.h`](src/decode.h) | Parser selection, decoded-value proof and guarded `loads` binding |
| [`src/lines.h`](src/lines.h) | Strict NDJSON batching over the single-document decoder |

[`hatch_build.py`](../../hatch_build.py) compiles only `adapter.c`; the headers
are implementation fragments in that translation unit. Read the
[source guide](src/README.md) before changing dispatch, lifetimes or proof limits.
After rebuilding, run from the repository root:
`uv run --no-sync python -m pytest tests/suites/product/serde -q`.
