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

Faithful Python 3.12–3.14 Base64 functions alongside unstd's existing text,
optional-padding and JSON helpers. [`pybase64`](https://github.com/mayeut/pybase64)
accelerates plain-byte encoding and strict decoding of plain ASCII inputs.

```python
from unstd.serde import b64

raw = b64.b64encode(data)  # bytes, exactly stdlib's default
data = b64.b64decode(raw)  # lenient by default, as in stdlib
data = b64.b64decode(raw, validate=True)  # strict alphabet/padding validation
raw = b64.b64encode(data, altchars=b"-_")  # arbitrary two-byte alternative alphabet

s = b64.encode(data)  # bytes -> str (standard, padded)
s = b64.encode(data, url=True, pad=False)  # base64url, JWT/bearer style
data = b64.decode(s)  # str|bytes -> bytes (validated — fails loud)
data = b64.decode(s, url=True)  # url-safe, padded or not
text = b64.decode_text(s)  # str|bytes -> str (utf-8, errors="replace")
blob = b64.encode_json(obj)  # any obj -> b64 of its compact JSON (arg/env smuggle)
obj = b64.decode_json(blob)  # inverse, via serde.jsonx
```

### Compatibility contract

`b64encode`, `b64decode`, `standard_b64encode`, `standard_b64decode`,
`urlsafe_b64encode` and `urlsafe_b64decode` preserve the corresponding stdlib
functions' defaults, return types, positional/keyword options and failures:

- Encoding returns `bytes`, accepts buffer exporters and retains padding.
  Decoding accepts bytes-like objects and ASCII text. Non-ASCII text raises
  `ValueError`; unsupported types, released views and noncontiguous encoding
  buffers retain stdlib's errors. Decoding accepts strided views by copying,
  just as CPython does. Inputs are not modified.
- `b64decode` defaults to `validate=False`: ignored bytes, leading/interior/
  excess padding and data after padding follow the running interpreter.
  `validate=True` rejects malformed input with the same exception class and
  diagnostic. `Error` is the original `binascii.Error`.
- Alternative alphabets follow stdlib translation semantics, including
  duplicated, overlapping and non-ASCII byte alternatives. Encoding requires
  bytes-like `altchars`; decoding also accepts ASCII text `altchars`. Invalid
  options and the order of their validation follow stdlib.
- Strict validation does **not** require zero unused pad bits. For example,
  `b64decode(b"Zh==", validate=True)` returns `b"f"`, as in stdlib. Encoding
  always emits zero pad bits. Neither strict decoding nor the URL helper
  promises a unique textual spelling.

`encodebytes` and `decodebytes` are stdlib's MIME helpers, including 76-column
wrapping, trailing newlines and their narrower buffer requirements. Legacy
file functions are available as `encode_file(input, output)` and
`decode_file(input, output)`. Their stdlib names conflict with unstd's existing
`encode` and `decode` helpers, so a whole-module import replacement requires
renaming those stream calls. Base16, Base32 and Base85 remain in `base64`.

The convenience API retains its distinct defaults: `encode` returns `str`,
`decode` validates standard input, and `decode(url=True)` accepts omitted
padding. The URL helper translates the URL alphabet, removes bytes stdlib
ignores, then appends enough `=` to reach a multiple of four. Existing `=`
characters remain in place so completed padding retains stdlib's termination
behavior. Thus `decode("AA!", url=True)` returns `b"\x00"`, and padded inputs
already accepted by stdlib keep their decoded value. Non-ASCII **text** remains
an error. `urlsafe_b64decode` itself keeps stdlib's padding requirement.

Installing the `serde` extra must not change bytes, accepted inputs, errors or
warnings. Native paths accept only exact builtin types; other buffers,
subclasses, lenient decoding and custom alphabets use stdlib. Native strict
decode failures return to stdlib for runtime-accurate behavior and diagnostics.
This boundary matters because pybase64 1.5 adopts Python 3.15 padding and URL
alphabet behavior, while this package tests Python 3.12–3.14. URL decoding
therefore uses stdlib rather than pybase64's unpadded decoder. Without the
extra, all operations use stdlib.

### Evidence and regression coverage

The contract follows the [Python 3.12 Base64 documentation](https://docs.python.org/3.12/library/base64.html),
[Python 3.14 Base64 documentation](https://docs.python.org/3.14/library/base64.html),
[strict-mode definition](https://docs.python.org/3.14/library/binascii.html#binascii.a2b_base64),
[CPython implementation](https://github.com/python/cpython/blob/3.14/Lib/base64.py)
and [CPython tests](https://github.com/python/cpython/blob/3.14/Lib/test/test_base64.py).
[RFC 4648 §3.5](https://www.rfc-editor.org/rfc/rfc4648.html#section-3.5)
explains unused bits and canonical encoding;
[pybase64's changelog](https://pybase64.readthedocs.io/en/stable/changelog.html)
and the [Python 3.15 documentation](https://docs.python.org/3.15/library/base64.html)
document the backend's changed semantics.

`tests/serde/test_b64_stdlib_parity.py` compares vectors,
options, errors, unusual buffers, subclasses and MIME/stream behavior with
the running stdlib. `tests/properties/test_b64_parity.py`
generates arbitrary bytes, Unicode, transport mutations, ignored characters
and padding, checking both the native backend and forced fallback. Named
examples retain the discovered failures, including trailing data after padding,
non-ASCII coercion and padding repair in the presence of ignored bytes.

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
n = ndjson.dump(rows, fp)  # streams; text/binary/gzip handles
rows = ndjson.load(fp)  # every record -> list
```

Every record is newline-terminated (append-safe). Blank lines are skipped on
read. Malformed lines raise `NDJSONDecodeError` with a 1-based `lineno` by
default; pass `skip_errors=True` for the lenient "drop the bad line" mode.
