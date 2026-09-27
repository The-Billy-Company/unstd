# `serde` — serialization/deserialization helpers

Part of [`unstd`](../README.md). Every module wraps a best-in-class
native codec behind a small, stdlib-faithful surface, and all JSON rides one
backend (orjson, via `jsonx`). The native backends live behind the **`serde`
extra** (`unstd[serde]`); without it, `jsonx`/`b64`/`ndjson` fall back to the
stdlib (same surface, slower) and the typed `structs` codecs — which have no
stdlib equivalent — raise a clear ImportError.

| Module                                      | Wraps                         | Use for                                                    |
| ------------------------------------------- | ----------------------------- | ---------------------------------------------------------- |
| [`jsonx`](#jsonx--accelerated-json-codec)   | `orjson`                      | untyped `dict`/`list` JSON on machine-to-machine paths     |
| [`b64`](#b64--accelerated-base64)           | `pybase64` (SIMD `libbase64`) | base64 of large payloads — audio, images, embeddings       |
| [`structs`](#structs--typed-msgspec-codecs) | `msgspec`                     | typed, validate-on-decode wire schemas (`msgspec.Struct`)  |
| [`ndjson`](#ndjson--newline-delimited-json) | `jsonx`                       | `.jsonl` streams — trace exports, eval datasets, snapshots |

## `jsonx` — accelerated JSON codec

Drop-in replacement for the stdlib `json` module on **machine-to-machine** JSON
(wire payloads, Redis envelopes, cache keys, tool results). Backed by
[`orjson`](https://github.com/ijl/orjson) for a several-fold encode/decode
speedup, wrapped in a stdlib-faithful surface.

```python
from unstd.serde import jsonx

body = jsonx.dumps(payload)  # -> str  (compact, UTF-8)
raw = jsonx.dumpb(payload)  # -> bytes (skips the str decode)
data = jsonx.loads(body)  # str | bytes -> Python
```

### Migrating a file

1. Replace `import json` with `from unstd.serde import jsonx`.
2. Rename call sites: `json.dumps` → `jsonx.dumps`, `json.loads` → `jsonx.loads`,
   `json.JSONDecodeError` → `jsonx.JSONDecodeError`, etc. The full stdlib
   surface used across the service (`dumps`, `loads`, `dump`, `load`,
   `JSONDecodeError`, `JSONDecoder`, `JSONEncoder`) is re-exported.

### Faithfulness & the one intentional difference

`jsonx.dumps` returns `str`, coerces non-string dict keys, passes `datetime`
through to `default` (so `dumps(dt, default=str)` matches stdlib), honors
`default`/`sort_keys`, and re-exports `JSONDecodeError` (orjson raises a subclass,
so `except` clauses keep working).

**Format difference (auto-recovered):** output is compact UTF-8 with non-ASCII
unescaped — valid JSON that parses identically but differs byte-for-byte from
stdlib's `\uXXXX` escaping and `", "`/`": "` spacing. Calls that need stdlib's
_exact bytes_ (`ensure_ascii=True`, `indent` ≠ 2, arbitrary `separators`, `cls`,
…) fall back to stdlib automatically. Without the `serde` extra installed, the
whole module falls back to stdlib `json`. Do **not** use `jsonx` for human-facing
pretty-print.

**Value differences (deliberate, _not_ recovered — orjson is a fixed-width
finite codec):**

| Input                           | `jsonx` (orjson)                          | stdlib `json`                   |
| ------------------------------- | ----------------------------------------- | ------------------------------- |
| `NaN` / `±Infinity`             | `null` (silent — `default` does not fire) | `NaN` / `Infinity` literals     |
| int outside ±2⁶³ (unsigned 2⁶⁴) | raises `TypeError`                        | arbitrary precision             |
| nesting past ~254 deep          | raises `TypeError`                        | recurses to interpreter limit   |
| `1e-05`                         | `0.00001` (fixed)                         | `1e-05` (exponent) — same value |

Each is pinned as a regression guard in `tests/serde/test_jsonx.py` and
`tests/properties/test_serde_parity.py`. Need a non-finite float to surface
loudly instead of becoming `null`? Use a typed [`structs`](#structs--typed-msgspec-codecs)
codec (it raises), not `jsonx`.

> **numpy arrays & scalars:** pass `numpy=True` to `dumps`/`dumpb`/`dump` to
> serialize an `ndarray` or numpy scalar directly instead of raising —
> orjson's native `OPT_SERIALIZE_NUMPY` on the fast path (contiguous
> int/float/bool/`datetime64` dtypes, native byte order), a `.tolist()`/`.item()`
> `default` on the stdlib fallback path. Needs numpy installed (not part of the
> `serde` extra itself — `unstd[pack]`/`unstd[rand]`/`unstd[audio]` already pull
> it in, or add it standalone). Without `numpy=True`, `default=float` (or
> `serde.structs.float_enc_hook` on the typed path) still works for a lone
> numpy *scalar*, since `numpy.float64`/`int64` subclass Python `float`/`int`
> but orjson dispatches on the exact type.

## `b64` — accelerated base64

The `base64.b64encode(x).decode("ascii")` / `b64decode(s).decode("utf-8",
"replace")` idioms fused into one verb per direction, with the alphabet and
padding as keywords, backed by
[`pybase64`](https://github.com/mayeut/pybase64) (SIMD `libbase64`, multi-GB/s)
for large payloads — PCM audio, PNG thumbnails, f16 embeddings.

```python
from unstd.serde import b64

s = b64.encode(data)  # bytes -> str (standard, padded)
s = b64.encode(data, url=True, pad=False)  # base64url, JWT/bearer style
data = b64.decode(s)  # str|bytes -> bytes (validated — fails loud)
data = b64.decode(s, url=True)  # url-safe, padded or not
text = b64.decode_text(s)  # str|bytes -> str (utf-8, errors="replace")
blob = b64.encode_json(obj)  # any obj -> b64 of its compact JSON (arg/env smuggle)
obj = b64.decode_json(blob)  # inverse, via serde.jsonx
```

Standard + url-safe **encode** are byte-for-byte identical to stdlib. Standard
`decode` validates, so non-alphabet input or a missing `=` raises `b64.Error`
(re-exported `binascii.Error`) instead of being silently truncated. Without the
`serde` extra, the helpers use stdlib `base64` (identical output, scalar speed).

## `structs` — typed `msgspec` codecs

Where a payload has a **fixed schema** (a `msgspec.Struct` — an HTTP response
cache entry, a Redis snapshot projection), build one reusable codec and reuse it (msgspec perf
guidance: constructing a codec is expensive, encode/decode is not).

```python
from unstd.serde import structs

CODEC = structs.Codec(MyStruct, enc_hook=structs.float_enc_hook)
raw = CODEC.encode(value)  # -> bytes (compact UTF-8)
value = CODEC.decode(raw)  # strict — raises on malformed / typed-wrong
value = CODEC.decode_or(raw, default=None)  # "malformed -> cache miss" convention
```

`decode_or` swallows only `msgspec.DecodeError` (which includes
`ValidationError`); any other exception propagates. `float_enc_hook` coerces
non-native numeric scalars (`numpy.float32`/`float64`, `Decimal`) to `float` so a
numpy-bearing struct serializes identically to one built from native floats.
Typed codecs have no stdlib fallback — importing `structs` without the `serde`
extra raises a clear `ImportError`.

`codec()`/`Codec()` also forward every constructor knob msgspec itself exposes:
`order="deterministic"` (sort dict keys/set elements) or `"sorted"` (also sort
struct fields by name) for canonical bytes worth hashing or diffing — msgspec's
own maintainer benchmarks this ~20-25% faster than `jsonx.dumps(..., sort_keys=True)`
for the same shape, since it never leaves the typed encoder; `decimal_format`/
`uuid_format` control how `Decimal`/`UUID` fields serialize; `strict=False` widens
decode coercion for loose upstream producers; `float_hook` redirects untyped
float decoding (e.g. to `Decimal`, for exact-precision round-tripping).

## `ndjson` — newline-delimited JSON

`.jsonl` — `\n`-separated JSON values, each independently parseable. What trace
exports, eval datasets, and snapshot canons are written as. A thin layer over
`jsonx` (same backend + fallback).

```python
from unstd.serde import ndjson

text = ndjson.dumps(rows)  # -> str (trailing \n per record)
raw = ndjson.dumpb(rows)  # -> bytes
rows = ndjson.loads(text)  # whole blob -> list
for row in ndjson.iter_loads(fp):  # lazy, never materializes the file
    ...
n = ndjson.write(fp, rows)  # streams; text/binary/gzip handles
```

Every record is newline-terminated (append-safe). Blank lines are skipped on
read. Malformed lines raise `NDJSONDecodeError` with a 1-based `lineno` by
default; pass `skip_errors=True` for the lenient "drop the bad line" mode.
