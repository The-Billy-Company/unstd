# `serde` — serialization/deserialization helpers

Part of [`unstd`](../README.md). Stdlib-compatible JSON and Base64 codecs,
typed codecs, and newline-delimited JSON helpers. All untyped JSON goes through `jsonx`,
which proves eligibility for native acceleration and uses stdlib otherwise.
The native backends live behind the **`serde`
extra** (`unstd[serde]`); without it, `jsonx`/`b64`/`ndjson` fall back to the
stdlib (same surface, slower) and the typed `structs` codecs — which have no
stdlib equivalent — raise a clear ImportError.

| Module                                      | Wraps                         | Use for                                                    |
| ------------------------------------------- | ----------------------------- | ---------------------------------------------------------- |
| [`jsonx`](#jsonx--accelerated-json-codec)   | stdlib `json` / guarded `orjson` | faithful JSON plus compact byte transport     |
| [`b64`](#b64--accelerated-base64)           | `pybase64` (SIMD `libbase64`) | base64 of large payloads — audio, images, embeddings       |
| [`structs`](#structs--typed-msgspec-codecs) | `msgspec`                     | typed, validate-on-decode wire schemas (`msgspec.Struct`)  |
| [`ndjson`](#ndjson--newline-delimited-json) | `jsonx`                       | `.jsonl` streams — trace exports, eval datasets, snapshots |

## `jsonx` — accelerated JSON codec

Drop-in replacement for the standard `json` function and codec API. Standard
calls preserve the running interpreter's stdlib semantics, using guarded
encoding through [`orjson`](https://github.com/ijl/orjson) on safe builtin values
and decoding through [`msgspec`](https://github.com/msgspec/msgspec).

```python
from unstd.serde import jsonx

body = jsonx.dumps(payload)  # str, exactly stdlib's defaults and formatting
raw = jsonx.dumpb(payload)  # extension: compact UTF-8 bytes
data = jsonx.loads(body)  # str | bytes | bytearray -> Python
```

### Migrating a file

Replace `import json` with `from unstd.serde import jsonx` and rename calls.
`dumps`, `dump`, `loads`, `load`, `JSONEncoder`, `JSONDecoder` and
`JSONDecodeError` follow stdlib behavior. The classes are stdlib's originals.
Custom codec constructor keywords pass through unchanged.

### Compatibility contract

The standard API preserves:

- **Exact output:** `ensure_ascii=True`, default spaces, indentation (including
  zero, negative and string indents), separators and finite float notation.
- **Numbers:** arbitrary precision integers subject to Python's configured digit
  limit, signed zero, `NaN` and infinities; `allow_nan=False` raises `ValueError`.
- **Unicode and input encodings:** lone surrogates in text, UTF-8/16/32 binary
  input and BOM handling according to the running stdlib.
- **Types and keys:** stdlib-supported subclasses, key coercion and sorting,
  duplicate coerced keys, `skipkeys`, and rejection of unsupported types.
  Dataclasses, UUIDs and plain enums require an explicit encoder or `default`.
- **Customization and failures:** hooks, custom classes, callback exceptions,
  circular references, interpreter limits, and complete decoder diagnostics.
- **Streams:** `dump` writes encoder chunks as stdlib does, including partial
  output before a later encoding or write failure; `load` supports text/binary
  files and duck-typed readers.

Installing optional dependencies never changes these standard behaviors.
The accelerated paths are deliberately narrower than the supported API:

- **Encoding:** no custom encoder, `default`, unknown keywords or custom option
  objects. The optional C guard validates bounded trees of exact builtin types,
  string keys and backend-range integers, then calls its trusted orjson encoder
  while retaining the GIL. No Python callback can mutate the tree between proof
  and encoding. Without the extension, a private snapshot validates at most 16
  values and 16 container levels. Floats and unsupported values use stdlib.
  Compact output and default spaces use native formatting. The extension adds
  spaces only outside quoted strings, preserving punctuation and escaped quotes
  in text. Indentation keeps the recursive snapshot proof. Top-level text uses
  stdlib's specialized encoder. ASCII output also excludes DEL, which stdlib
  escapes differently.
- **Decoding:** exact `str`/`bytes` with no explicit options. A reusable msgspec
  decoder constructs arbitrary-precision integers and correctly rounded floats.
  The optional extension can use orjson for larger documents after a bounded
  native proof excludes numeric coercions. Interpreter limits still apply to
  the entire source, including discarded duplicate values. Failures,
  including integer digit limits, Unicode extensions and malformed input, parse
  the original document with stdlib for authoritative behavior and diagnostics.
  Explicit options and caller callbacks go directly to stdlib.
- **Other cases:** common builtin formatting configurations reuse a bounded cache
  of stateless stdlib encoders; custom behavior goes directly to stdlib. `load`
  reads once and shares the same decoder; `dump` keeps stdlib's streaming writes.
  Eligibility limits only choose the codec and never reject supported data.

Both encoders and decoders preserve interpreter recursion failures. Backend
errors fall back to stdlib for the original input and its diagnostics.
A native codec cannot provide this entire contract by itself; the bounded
proofs and fallback paths are part of the implementation.

Speed depends on the payload and Python version. The benchmark harness compares
identical outputs and separately measures native paths and fallback records.
The [Billy fixture comparison](../../../bench/billy-serde-optimization-results.md)
retains release regressions as well as gains. Compatibility checking has a cost relative
to the permissive 2.1 codec; measured results are not a universal speed promise.

**Compact bytes:** `dumpb` equals
`json.dumps(obj, ensure_ascii=False, separators=(",", ":"), ...).encode()` with
its `sort_keys` and `default` options. It uses the same native guard or snapshot
and falls back for all other values, including floats, so notation also matches
stdlib across backends. Lone surrogates raise the same UTF-8 encoding error.
`canonical` retains its sorted, compact, ASCII-escaped identity format; it is
not an implementation of RFC 8785.

Platform wheels include the optional guard for each CPython version. Source
builds use a compiler when available and otherwise retain the portable
implementation; no runtime dependency or public API is added. Use a platform
wheel for the full JSON and NDJSON speedups. On CPython 3.14, native dispatch
preserves the public Python function objects, signatures and annotations, and
resumes ordinary Python execution for tracing, profiling or monitoring.

**Numpy extension:** with the default encoder, `numpy=True` on
`dumps`/`dump`/`dumpb` converts arrays with `.tolist()` and scalars with `.item()`
before calling a user-supplied `default` for other objects. Conversion and float
precision are independent of the installed backend. Numpy must be installed
separately (or through `unstd[pack]`, `unstd[rand]` or `unstd[audio]`). When
`cls` is explicit, `dump`/`dumps` forward `numpy` to that custom encoder as an
ordinary constructor keyword.

### Evidence and regression coverage

The contract is based on the [Python 3.12 JSON documentation](https://docs.python.org/3.12/library/json.html)
(the package's supported floor), [CPython's implementation](https://github.com/python/cpython/tree/3.12/Lib/json)
and [regression tests](https://github.com/python/cpython/tree/3.12/Lib/test/test_json),
and [orjson's documented differences](https://github.com/ijl/orjson).
`tests/suites/product/serde/parity/test_jsonx_encode_parity.py` and
`test_jsonx_decode_parity.py` compare
formatting, values, types, hooks, errors and stream behavior to the running
stdlib, with and without the backend.
`tests/suites/product/serde/properties/test_serde_parity.py`
generates broad values, options, Unicode, binary encodings and malformed input;
its comparator checks type, key order, NaN and negative zero as well as equality.
The fast-path suite also asserts actual backend use, snapshot isolation, fallback
dispatch, and recursion boundaries rather than letting every example fall back.
CI covers Python 3.12, 3.13 and 3.14, dependency floors and a real base install.

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

`tests/suites/product/serde/parity/test_b64_parity.py` compares vectors,
options, errors, unusual buffers, subclasses and MIME/stream behavior with
the running stdlib. `tests/suites/product/serde/properties/test_b64_parity.py`
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
unsupported numeric scalars (`numpy.float32`/`float64`) to `float` so a
numpy-bearing struct serializes identically to one built from native floats.
Msgspec handles `Decimal` natively before consulting the hook: it emits JSON
strings by default. Use `Codec(..., decimal_format="number")` for numeric
Decimal output, including when `float_enc_hook` is installed.
Typed codecs have no stdlib fallback — importing `structs` without the `serde`
extra raises a clear `ImportError`.

`Codec()` also forwards the supported msgspec encoder and decoder options:
`order="deterministic"` (sort dict keys/set elements) or `"sorted"` (also sort
struct fields by name) for deterministic bytes worth hashing or diffing;
`decimal_format`/
`uuid_format` control how `Decimal`/`UUID` fields serialize; `strict=False` widens
decode coercion for loose upstream producers; `float_hook` redirects untyped
float decoding (e.g. to `Decimal`, for exact-precision round-tripping).

## `ndjson` — newline-delimited JSON

`.jsonl` — `\n`-separated JSON values, each independently parseable. What trace
exports, eval datasets, and snapshot canons are written as. A thin layer over
`jsonx` for untyped records, or a reusable `structs.Codec` for typed records.

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
The optional extension batches whole-blob reads while keeping physical newline
boundaries. Streaming readers keep their lazy per-record behavior.

Pass `codec=` to decode directly into a validated schema, with no intermediate
dictionary and second validation pass. Construct the codec once and reuse it:

```python
import msgspec

from unstd.serde import ndjson, structs


class Event(msgspec.Struct):
    name: str
    count: int


EVENTS = structs.Codec(Event)
with open("events.jsonl", "rb") as fp:
    for event in ndjson.iter_loads(fp, codec=EVENTS):
        print(event.name)

raw = ndjson.dumpb([Event("saved", 1)], codec=EVENTS)
events = ndjson.loads(raw, codec=EVENTS)  # list[Event]
```

All six read/write functions accept `codec=`. Typed reads follow that codec's
strictness and conversion settings; writes use its encoder and append one newline
per record. Like the codec itself, writing does not validate field types at runtime.
Typed reads require the existing `serde` extra; untyped operations share jsonx's
decoder and keep the stdlib fallback.

Reader `on_error(error)` callbacks receive `NDJSONDecodeError` once for each
rejected row, before the default raise or `skip_errors=True`. Its `lineno` counts
physical lines, including blanks; `err` retains the original decode/validation
exception and schema field path. Exceptions from the callback, or codec hooks
outside msgspec's decode-error family, propagate. Typed decoding processes each
row once, so a later schema failure cannot replay earlier conversion hooks.
