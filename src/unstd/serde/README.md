---
doc_radar:
  sentinels:
    - file: src/unstd/serde/encoding.py
      contains: ['def _exact_encode(', 'def _encoder(']
    - file: src/unstd/serde/recursion.py
      contains: ['def _check_headroom[', 'def _recursion_result[']
---

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
  objects. The optional C guard validates depth-bounded trees of exact builtin types,
  string keys and backend-range integers, then calls its trusted orjson encoder
  while retaining the GIL. No Python callback can mutate the tree between proof
  and encoding. Without the extension, a private snapshot validates at most 16
  values and 16 container levels. Native public proofs validate at most 262,144
  values and 16 container levels, counting every repeated alias. Larger or
  unsupported values use stdlib. Finite floats in two-space indented output on
  Python 3.12 retain CPython's number spellings. Eligible finite-float trees use
  a bounded native writer with CPython's formatter and operation-local spelling
  caches; it shares the visit budget with the ordinary proof, requires compact
  integers, limits strings to 256 code points and private output to 1 MiB, and
  falls back outside that domain.
  Eligible shared-row lists can reuse up to 32 completed flat branch spans
  within the same call. Reuse requires the same object and depth, and every
  repeated logical value still consumes the budget. The complete output is
  allocated each time; mutations between calls remain visible.
  Compact output and default spaces use native formatting. The extension adds
  spaces only outside quoted strings, preserving punctuation and escaped quotes
  in text. Two-space indentation uses a native proof that reserves stdlib's
  recursive generator frames before encoding. Native ASCII conversion preserves
  DEL, BMP and surrogate-pair escape spellings without encoding the tree again.
  Top-level text uses stdlib's specialized encoder.
- **Decoding:** exact `str`/`bytes` with no explicit options. A reusable msgspec
  decoder constructs arbitrary-precision integers and correctly rounded floats.
  The optional extension can use orjson for larger documents after a bounded
  native proof excludes numeric coercions. Its visit budget follows the original
  source length, so wide arrays and objects no longer hit a fixed 65,536-value
  limit and discard a complete parse. The proof retains its 64-container depth
  cap. A bounded source prefix selects the precise parser directly for
  float-bearing objects and arrays, avoiding a second parse. Interpreter limits still apply to
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
The [large-document decoding profile](../../../bench/profiles/json_decode_scale.py)
compares the former visit-limit boundary, independent record arrays, precision
fallbacks and text controls; its [results](../../../bench/reports/serde/json-decode-scale.md)
retain exact-output checks and paired timings.
The [JSONX throughput profile](../../../bench/profiles/jsonx_throughput.py)
compares saved and current sources across encoding, decoding, formatting and
stream operations, retaining raw timings, stdlib controls and loaded source
hashes.
The [existing-only comparison](../../../bench/profiles/jsonx_existing.py)
rotates original, first-pass and candidate sources through the unchanged
Python registry/core fixtures, with correctness oracles and separate profiles.
The [second-pass results](../../../bench/reports/serde/jsonx-second-pass.md) and
[memory-safety qualification](../../../bench/reports/serde/jsonx-second-safety.md)
retain gains, measured losses, source hashes and sanitizer/failure-injection
evidence. The [shared-branch qualification](../../../bench/reports/serde/jsonx-100x.md)
reports cases exceeding 100× separately from the existing independent records;
uniform 100× acceleration across arbitrary JSON is not established.
The [serde reports](../../../bench/reports/serde/README.md) retain release
regressions as well as gains. Compatibility checking has a cost relative to the
permissive 2.1 codec; measured results are not a universal speed promise.

### Stream a JSON array

`iterload` processes an ordinary JSON-array export one item at a time, including
from a gzip stream. It works in the base install and uses the same accelerated
item decoder when the `serde` extra is present.

```python
from unstd.compress import gzipx
from unstd.serde import jsonx

with gzipx.open("events.json.gz", "rb") as source:
    for event in jsonx.iterload(source):
        process(event)
```

The input must be a top-level array. Text and binary streams support short,
sized reads; binary encoding detection accepts UTF-8/16/32 with or without a
BOM, following stdlib JSON. `chunk_size=65536` bounds each read in bytes or text
characters. Buffering follows the chunk and largest encoded/decoded item,
excluding values retained by the caller or decoder hooks. With the native
extension and an unchanged default decoder, complete records share one bounded
batch decode. UTF-8 binary input stays in bytes through that path; UTF-16/32
input is decoded incrementally. An incomplete short suffix can join one next
window, while large records keep the incremental item path. Invalid batches
resume the portable walker with their original array state and diagnostics.

Options such as `parse_float=Decimal`, `object_hook`, and `cls` apply separately
to each item. The outer array is framing only. The caller owns the source;
early termination leaves it open and may have read ahead by one chunk. Exhaust
the iterator to validate the closing bracket and trailing input. A late error
can follow valid yielded items. `JSONDecodeError` locations refer to the current
item or input chunk, rather than the complete source document.

The native batch path preserves bounded memory while reducing decoding overhead.
Custom decoder options and the portable path retain item-by-item decoding. The
[memory profile](../../../bench/profiles/json_array_stream.py) compares identical
exports with independent aggregates, output hashes, first-item reads and fresh
process peak memory; its
[optimization comparison](../../../bench/reports/serde/json-array-stream-speedup.md)
retains the original streaming path and eager loader as controls.

### Write a JSON array from a generator

`dump_array` exports an iterable as one compact JSON array without building a
list of records or a complete encoded document. It returns the record count.

```python
from unstd.compress import gzipx
from unstd.serde import jsonx

with gzipx.open("export.json.gz", "wb") as destination:
    count = jsonx.dump_array(generate_events(), destination)
```

Each record uses `dumpb`, including its `sort_keys`, `default`, and `numpy`
options. Binary sinks receive UTF-8 bytes; `io.TextIOBase` sinks receive Unicode
and apply their own encoding policy. `chunk_size=65536` batches output and
bounds each write request in bytes or text characters. Short writes are
completed before the next chunk. Standard `io.IOBase` sinks returning zero or
`None` raise `BlockingIOError`; generic write-only sinks may acknowledge a full
write with `None`. Other invalid counts raise `OSError`.

Memory follows the chunk and largest record, excluding values retained by the
producer or callbacks. Reusing and mutating one record in a generator works:
each record is encoded before requesting the next. Encoding options and
recursion limits apply per record; the outer array is framing only. The base
install works without optional backends.

The opening bracket is written before consuming the iterable. Batching can
consume several records before a sink error. Encoder, iterator and I/O errors
propagate, leaving partial output; buffered records may not yet have reached
the sink. The closing bracket is written only after successful consumption.
The caller owns flushing, closing and any atomic-file replacement policy.
The [write profile](../../../bench/profiles/json_array_write.py) compares exact
output hashes, fresh-process peak memory, elapsed time and separate write-call
diagnostics against the existing eager export APIs. Its
[retained measurements](../../../bench/reports/serde/json-array-write.md) include
the full generated-export pipeline and the faster small-corpus bulk alternative.

**Compact bytes:** `dumpb` equals
`json.dumps(obj, ensure_ascii=False, separators=(",", ":"), ...).encode()` with
its `sort_keys` and `default` options. It uses the same native guard or snapshot
and falls back for other values. Eligible finite floats use CPython's formatter,
so notation also matches stdlib across backends. Lone surrogates raise the same
UTF-8 encoding error.
`canonical` retains its legacy sorted, compact, ASCII-escaped format, including
the default conversion of unsupported values through `str`. That conversion
can produce unstable values or collide with ordinary strings. Its bytes are
unchanged for existing persisted identities; neither identity API implements
RFC 8785/JCS.

For new identities, use `canonical_strict(value)`. It accepts exact builtin
dicts with exact string keys, lists, strings, integers, finite floats, booleans,
and `None`. Unsupported objects and subclasses, tuples, sets, non-string keys,
non-finite numbers, surrogate code points (including explicit surrogate pairs),
and cycles raise instead of being coerced.
No conversion hook runs. Shared acyclic branches are allowed, and every mutable
branch is copied before encoding so validation and encoding use the same values.
The optional native copy owns source items across allocation and keeps partial
outputs private across collection. Changes to the Python walker, relevant
builtins, finiteness check or observers select the original Python copy. Both
paths still call the identity encoder in its original lookup order.

```python
jsonx.canonical_strict({"b": 2, "a": [True, None]})
# b'{"a":[true,null],"b":2}'
jsonx.canonical_strict(b"a")  # TypeError; no collision with the string "b'a'"
```

The version-1 format matches `canonical` on this restricted domain: sorted
keys, compact separators, ASCII escapes, and stdlib number spellings. Unicode
normalization is not applied; `1`/`1.0` and positive/negative zero retain their
distinct spellings. Normalize such values explicitly if your application treats
them as the same identity. Integer conversion and recursion limits still apply.
Any future incompatible identity format will use a separate API.

Platform wheels include the optional guard for each CPython version. Source
builds use a compiler when available and otherwise retain the portable
implementation; no runtime dependency or public API is added. Use a platform
wheel for the full JSON and NDJSON speedups. On CPython 3.14, native dispatch
preserves the public Python function objects, signatures and annotations, and
resumes ordinary Python execution for tracing, profiling or monitoring.

Release wheels verify active native bindings and representative float, ASCII,
decode, identity and NDJSON paths with
[`json_acceleration.py`](../../../tests/tooling/runtime/json_acceleration.py).
The performance workflow compares installed JSON record workloads on each
Linux/macOS worker with native dispatch required, retaining raw measurements
before failing a slowdown above 15%. Run the same qualification with the
deployment interpreter and the `serde` extra to detect an installation that has
lost its native paths.
The [Foundry qualification](../../../bench/reports/serde/jsonx-server.md) retains
native code/source equality, installed wheel and source-build contracts, paired
Linux/macOS gains, and shared-host timing variation.

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
CI covers Python 3.12 through 3.15, dependency floors and a real base install.

## `b64` — accelerated base64

Faithful Python 3.12–3.15 Base64 functions alongside unstd's existing text,
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
- `b64decode` normally defaults to `validate=False`: ignored bytes, leading/interior/
  excess padding and data after padding follow the running interpreter.
  `validate=True` rejects malformed input with the same exception class and
  diagnostic. `Error` is the original `binascii.Error`.
- On Python 3.15, `b64encode` also accepts `padded` and `wrapcol`, and
  `b64decode` accepts `padded`, `ignorechars` and `canonical`. Specifying
  `ignorechars` makes validation default to true. `urlsafe_b64encode` and
  `urlsafe_b64decode` accept `padded`; URL decoding defaults to unpadded mode.
- Alternative alphabets follow stdlib translation semantics, including
  duplicated, overlapping and non-ASCII byte alternatives. Encoding requires
  bytes-like `altchars`; decoding also accepts ASCII text `altchars`. Invalid
  options and the order of their validation follow stdlib.
- Strict validation does **not** require zero unused pad bits. For example,
  `b64decode(b"Zh==", validate=True)` returns `b"f"`, as in stdlib. Encoding
  always emits zero pad bits. Python 3.15's `canonical=True` additionally rejects
  nonzero pad bits. Neither ordinary strict decoding nor the URL helper
  promises a unique textual spelling.

`encodebytes` and `decodebytes` are stdlib's MIME helpers, including 76-column
wrapping, trailing newlines and their narrower buffer requirements. Legacy
file functions are available as `encode_file(input, output)` and
`decode_file(input, output)`. Their stdlib names conflict with unstd's existing
`encode` and `decode` helpers, so a whole-module import replacement requires
renaming those stream calls. Base16, Base32 and Base85 remain in `base64`.

The convenience API retains its distinct defaults: `encode` returns `str`,
`decode` validates standard input, and `decode(url=True)` accepts omitted
padding. On Python 3.12–3.14 the URL helper translates the URL alphabet, removes bytes stdlib
ignores, then appends enough `=` to reach a multiple of four. Existing `=`
characters remain in place so completed padding retains stdlib's termination
behavior. Thus `decode("AA!", url=True)` returns `b"\x00"`, and padded inputs
already accepted by stdlib keep their decoded value. Non-ASCII **text** remains
an error. On Python 3.15 the URL helper uses stdlib's unpadded decoding directly,
including its treatment of interior `=` and warnings for `+` or `/` in URL data.
`urlsafe_b64decode` itself follows the running stdlib's padding default.

Installing the `serde` extra must not change bytes, accepted inputs, errors or
warnings. Native paths accept only exact builtin types; other buffers,
subclasses, lenient decoding and custom alphabets use stdlib. Native strict
decode failures return to stdlib for runtime-accurate behavior and diagnostics.
On Python 3.15 the stdlib-named functions and URL convenience decoder use stdlib
directly to preserve the expanded options and warnings. On older interpreters,
the wrappers preserve their original padding and alphabet behavior even though
pybase64 1.5 has adopted Python 3.15 semantics. Without the extra, all operations
use stdlib.

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
`ValidationError`) and the `UnicodeError` msgspec raises for non-UTF-8 string
content; any other exception propagates. `float_enc_hook` coerces
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

## Implementation layout

The package is a flat codec leaf: each file owns a concrete concern.

| File | Responsibility |
| --- | --- |
| [`jsonx.py`](jsonx.py) | JSON entry points, backend selection, native array batches and incremental input decoding |
| [`_stream.py`](_stream.py) | Portable JSON-array framing and per-item decoder fallback |
| [`encoding.py`](encoding.py) | Exact-output eligibility proofs, bounded snapshots and reusable encoders |
| [`recursion.py`](recursion.py) | The two Python frames needed to retain stdlib recursion headroom |
| [`b64.py`](b64.py) | Base64 codec and transport conveniences |
| [`ndjson.py`](ndjson.py) | Line-delimited streams and typed record integration |
| [`structs.py`](structs.py) | Typed msgspec schemas and reusable codecs |

`__init__.py` introduces the namespace. `_json_native.pyi` describes the optional
compiled extension; its sources live in [`native/serde/`](../../../native/serde/README.md).
The implementation filenames describe their roles and preserve all public codec
imports. Run `uv run --no-sync python -m pytest tests/suites/product/serde -q`
from the repository root.
