# Native JSON dispatch

Private `unstd.serde._json_native` extension for GIL builds of CPython. It
requires `orjson`; decoding also needs `msgspec`. The Python
[`jsonx`](../../src/unstd/serde/jsonx.py) and
[`ndjson`](../../src/unstd/serde/ndjson.py) wrappers own compatibility and fallback.

| Source | Responsibility |
| --- | --- |
| [`src/adapter.c`](src/adapter.c) | Module state, backend binding, encoding proofs, spacing and float normalization |
| [`src/encode.h`](src/encode.h) | Guarded `dumpb` vectorcall binding on CPython 3.14 |
| [`src/decode.h`](src/decode.h) | Parser selection, decoded-value proof and guarded `loads` binding |
| [`src/lines.h`](src/lines.h) | Strict NDJSON batching over the single-document decoder |
| [`src/escape.h`](src/escape.h) | Trusted UTF-8 output to stdlib ASCII escape spelling |
| [`src/repeat.h`](src/repeat.h) | Bounded float-record encoding with operation-local spelling caches |
| [`src/identity.h`](src/identity.h) | Owned strict-identity snapshots with guarded Python fallback |

[`hatch_build.py`](../../hatch_build.py) compiles only `adapter.c`; the headers
are implementation fragments in that translation unit. Read the
[source guide](src/README.md) before changing dispatch, lifetimes or proof limits.
From the repository root, run
`uv run --no-sync python -m pytest tests/suites/product/serde -q` after rebuilding.
