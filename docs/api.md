---
doc_radar:
  sentinels:
    - file: ../src/unstd/time/timeutil.py
      contains: ['wall_ns = _time.time_ns', 'perf_ns = _time.perf_counter_ns', 'strptime = datetime.strptime', 'combine = datetime.combine', 'from_iso = datetime.fromisoformat', 'def parse_iso(']
    - file: ../src/unstd/yaml/__init__.py
      contains: ['if name in ("pyyaml", "ruamel"):', 'globals()[name] = backend']
    - file: ../tests/suites/assurance/gates/packaging/test_public_api.py
      contains: ['def test_public_inventory_retains_all_exports(']
---

# Public API inventory

This is the supported import surface of `unstd`. Source links provide the full
signatures, overloads, errors, and behavioral contracts. Package and module
names remain stable when their private implementation is reorganized.

## Imports and package boundaries

The [root package](../src/unstd/__init__.py) provides the lazy `__version__`
attribute. Importing `unstd` does not eagerly import every package or optional
dependency. Import the packages or domain modules that the application uses:

```python
from unstd import clone, containers, fs, iters, proc, rand, rex, toml, yaml
from unstd.audio import wavx
from unstd.compress import gzipx
from unstd.crypto import digest, tls, token
from unstd.ids import uid
from unstd.model import FrozenModel, StrictModel, StrictSettings
from unstd.pack import binpack
from unstd.rand import crypto
from unstd.serde import b64, jsonx, ndjson, structs
from unstd.text import fuzz
from unstd.time import timeutil, zoned
```

These examples identify the available import paths; individual applications
need only their relevant imports. Some capabilities require optional extras,
as described below. Importing a group package such as `unstd.crypto` does not
require importing all of its submodules.

Python module attributes can include imported dependencies and private helper
names. Those incidental attributes are outside the supported API listed here.
Existing backend availability flags are retained for compatibility and are
listed separately at the end of this document.

## Audio

Import `from unstd.audio import wavx`.

| Module | Exported API |
| --- | --- |
| [`unstd.audio.wavx`](../src/unstd/audio/wavx.py) | `Writer`, `blocks`, `decode_pcm`, `read`, `wrap_pcm`, `write` |

`read` returns `(samples, samplerate)`; `blocks` yields sample chunks. `write`
writes samples, `wrap_pcm` adds a WAV header to a contiguous PCM buffer, and
`decode_pcm(raw, *, channels=1, width=2, dtype="float32")` converts raw frames
directly to independent samples. Mutable and typed buffers are accepted; sizes
count bytes. `decode_pcm` rejects incomplete frames and nonpositive channels.
The `audio`
extra provides soundfile and NumPy. The stdlib fallback supports PCM16 with
Python sample lists; NumPy without soundfile also supports the documented
8/16/32-bit read and PCM16/PCM32 write paths.

`Writer` owns incremental WAV output:

```python
Writer(path_or_file, samplerate, *, channels=1, subtype="PCM_16")
writer.write(samples) -> None
writer.close() -> None
```

It also supports `with Writer(...) as writer`; entering returns the writer and
exiting closes it. Channel count and subtype are fixed at construction. Output
must support the seeking needed to finalize the WAV header.

`PathOrFile` and `Samples` remain named annotation aliases in `wavx`; they are
not additional wildcard exports.

## Cloning

Import `from unstd import clone`.

| Module | Exported API |
| --- | --- |
| [`unstd.clone`](../src/unstd/clone/__init__.py) | `asdict`, `copy`, `deep`, `deepcopy` |
| [`unstd.clone.structural`](../src/unstd/clone/structural.py) | The same four objects, available through the existing module path |

`copy` and `deepcopy` are the original stdlib functions. `deep` is the structural
clone operation; `asdict` preserves the documented `dataclasses.asdict` behavior.
`deep` preserves shared-reference identity within each clone: repeated references
to one source object reach one cloned object. Owned mutable data is independent
of the source; immutable atoms may stay shared. Foreign copy hooks receive the
same memo and control their own result. `asdict` retains the stdlib conversion's separate copies at structural positions.
No extra is needed.

## Compression

Import `from unstd.compress import gzipx`.

| Module | Exported API |
| --- | --- |
| [`unstd.compress.gzipx`](../src/unstd/compress/gzipx.py) | `BadGzipFile`, `GzipFile`, `compress`, `decompress`, `open` |
| [`unstd.compress.zlibx`](../src/unstd/compress/zlibx.py) | zlib constants, complete-buffer codecs, compressor objects, checksums; original stdlib incremental decompressor |
| `unstd.compress.bz2`, `.lzma`, `.zlib`, `.zipfile`, `.tarfile` | Complete original stdlib modules, including qualified imports |
| `unstd.compress.zstandard` | Complete original `zstandard` module with `unstd[zstd]` |
| `unstd.compress.zstd` | Complete original `compression.zstd` module on Python 3.14+ |
| `unstd.compress.libarchive` | Original `libarchive-c` package and qualified child modules with `unstd[archives]` and a native libarchive library |

These preserve the running interpreter's gzip API and defaults. `BadGzipFile`
is the stdlib exception. `GzipFile` exposes the stdlib binary file interface,
including reading, iteration, seeking, writing, flushing, closing, and context
management. `open` also supports the stdlib text modes. The `compress` extra
enables native codecs; a base install uses stdlib gzip.

Linux streaming selects zlib-ng; macOS retains platform libz. The interpreter
still supplies framing, member checks, output budgets and error-delivery order.
`zlibx` accelerates compression, complete-buffer decoding and checksums while
retaining the interpreter's exact incremental decompressor state. Both accelerated
interfaces preserve recovered bytes; compressed output can differ by engine.
Use `from unstd.compress import zlib` where persisted compressed bytes must match
the running interpreter's stock codec.

Original-module ports preserve module, class, exception and native callback
identity and add no per-operation adapter. Their upstream version-specific
contracts remain in force. Archive policies, cumulative expansion limits and
Zstandard frame/window checks remain caller responsibilities. See the
[port contract matrix](../bench/reports/compress/port-contracts.md) for
consumer operation mapping and verification.

## Containers

Import `from unstd.containers import Map, SortedDict, SortedList, SortedSet`.

| Module | Supported API |
| --- | --- |
| [`unstd.containers`](../src/unstd/containers/__init__.py) | `Map`, `SortedDict`, `SortedList`, `SortedSet` |
| [`unstd.containers.persistent`](../src/unstd/containers/persistent.py) | `Map` |
| [`unstd.containers.sorted`](../src/unstd/containers/sorted.py) | `SortedDict`, `SortedList`, `SortedSet` |

With the `containers` extra, `Map` is the actual `immutables.Map`. Sorted
collections use compatible subclasses of `sortedcontainers` when the optional
CPython kernels are available, and the original classes otherwise. Constructors,
methods, generic typing and iteration retain the backend API; accelerated sorted
class identities differ from the upstream classes. For example, `Map.set` and
`Map.delete` return new persistent mappings. Missing backends leave importable
placeholders that raise an actionable `ImportError` on construction.
Each family loads on first access to its types or submodule.

## Cryptography and TLS

Import `from unstd.crypto import digest, token, tls`.

| Module | Exported API |
| --- | --- |
| [`unstd.crypto.digest`](../src/unstd/crypto/digest.py) | `DIGEST_BYTES`, `Stream`, `derive_key`, `hex`, `legacy_blake2b`, `raw` |
| [`unstd.crypto.token`](../src/unstd/crypto/token.py) | `KEY_BYTES`, `SEPARATOR`, `TAG_BYTES`, `equal`, `mac_hex`, `mac_raw`, `mint`, `prepare_verifier`, `verify`, `verify_mac` |
| [`unstd.crypto.tls`](../src/unstd/crypto/tls.py) | `LEGACY_MINIMUM_VERSION`, `MINIMUM_VERSION`, `TlsContext`, `client_context` |

`digest` and `token` require the `crypto` extra. BLAKE3 outputs, derived keys,
token envelopes, and the explicit legacy BLAKE2b helper retain their algorithms
and byte formats. `DIGEST_BYTES`, `KEY_BYTES`, and `TAG_BYTES` are each `32`;
`SEPARATOR` is `"."`.

`Stream` provides incremental BLAKE3 hashing:

```python
Stream(chunks=())
stream.update(chunk: bytes) -> Stream
stream.raw() -> bytes
stream.hex(n: int | None = None) -> str
```

`update` returns the same stream. Reading a digest does not finalize or consume
the stream; later updates remain valid. Chunks are concatenated without adding
separators. The `n` option follows `digest.hex`: it counts digest bytes and
produces `2n` hex characters.

`token.prepare_verifier(*, max_token_chars=8192)` returns a callable with the
surface `verify(key: bytes, token: str) -> bytes | None`. The returned callable
retains the selected limit and backend references; keys and tokens remain call
arguments. `token.verify` takes the same size policy as a keyword argument.
Passing `max_token_chars=None` selects no size limit.

`tls` is pure stdlib. `TlsContext` is an annotation alias for `ssl.SSLContext`;
`client_context` returns an actual stdlib context. Its parameters are
`cafile=None`, `certfile=None`, `keyfile=None`, and
`minimum_version=MINIMUM_VERSION`, all keyword-only. The default minimum is
TLS 1.3; `LEGACY_MINIMUM_VERSION` names TLS 1.2. Certificate and hostname
verification remain enabled.

## Filesystem operations

Import `from unstd import fs`.

| Module | Exported API |
| --- | --- |
| [`unstd.fs`](../src/unstd/fs/__init__.py) | `atomic_replace`, `atomic_write`, `atomic_writer`, `ensure_dir`, `sync_dir`, `walk_files` |

`ensure_dir` returns a `Path`; `walk_files` yields paths. `atomic_writer` is a
context manager for writing the replacement file; `atomic_write` and
`atomic_replace` perform complete operations and return the destination `Path`.
All three write operations accept `durable=False`, `True`, or `"strict"`.
Strict mode requires directory-sync acknowledgments before and after replacement;
a post-commit failure reports that the new data is already committed.
`sync_dir(path, *, strict=False)` returns `None` and suppresses OS errors by
default, while `strict=True` reports them.

This package needs no extra or third-party runtime dependency. Ordinary
GIL-enabled Linux CPython builds use the bundled native `atomic_write` backend
for exact bytes and default UTF-8 text with an existing parent. Other platforms,
unsupported inputs, custom OS/path bindings, and unavailable native capabilities
retain the complete Python path. Native I/O errors propagate without retrying
the write. `atomic_writer` retains the Python streaming implementation.
The [filesystem guide](../src/unstd/fs/README.md) describes admission, mode
preservation, cancellation, cleanup notes, and the durability limits.

## Identifiers

Import `from unstd.ids import uid`.

| Module | Exported API |
| --- | --- |
| [`unstd.ids.uid`](../src/unstd/ids/uid.py) | `derive`, `new`, `new_hex`, `parse` |

`new()` returns a hyphenated UUIDv7 string; `new_hex()` returns its 32-character
hex form. `parse` is the original stdlib `uuid.UUID` class and returns UUID
objects. `derive` is the original stdlib `uuid.uuid5` function; identical
namespace/name inputs retain their existing UUIDv5 values. The `ids` extra
accelerates minting, with the documented stdlib fallback.

## Iterators

Import `from unstd import iters`, or import individual combinators from it.

| Module | Exported API |
| --- | --- |
| [`unstd.iters`](../src/unstd/iters/__init__.py) | `batched`, `chunked`, `first`, `flatten`, `ilen`, `last`, `nth`, `pairwise`, `partition`, `runs`, `take`, `unique_everseen`, `unique_justseen`, `windowed` |
| [`unstd.iters.consecutive`](../src/unstd/iters/consecutive.py) | `chunked`, `runs`, `windowed` |
| [`unstd.iters.distinct`](../src/unstd/iters/distinct.py) | `unique_everseen`, `unique_justseen` |
| [`unstd.iters.reshape`](../src/unstd/iters/reshape.py) | `flatten`, `partition` |
| [`unstd.iters.select`](../src/unstd/iters/select.py) | `first`, `ilen`, `last`, `nth`, `take` |

`batched` and `pairwise` are the stdlib objects. Existing submodule imports
resolve to the same combinators exposed by `iters`. This package needs no extra.

## Models and settings

Import `from unstd.model import StrictModel, FrozenModel, StrictSettings`.

| Module | Exported API |
| --- | --- |
| [`unstd.model`](../src/unstd/model/__init__.py) | `FrozenModel`, `StrictModel`, `StrictSettings` |
| [`unstd.model.base`](../src/unstd/model/base.py) | `FrozenModel`, `StrictModel` |
| [`unstd.model.settings`](../src/unstd/model/settings.py) | `StrictSettings` |

These are subclassable model bases with inherited Pydantic interfaces, including
validation, field access, serialization, JSON schema, and model configuration.
They add no separate instance methods. `StrictModel` forbids unknown fields and
accepts names or aliases; `FrozenModel` adds frozen instances; `StrictSettings`
ignores unknown root inputs and accepts names or aliases. The `model` extra is
required. The same class objects remain available at the existing submodule paths.

## Binary packing

Import `from unstd.pack import binpack, ragged`.

| Module | Exported API |
| --- | --- |
| [`unstd.pack.binpack`](../src/unstd/pack/binpack.py) | `Struct`, `calcsize`, `error`, `iter_unpack`, `pack`, `pack_array`, `pack_array_portable`, `pack_into`, `unpack`, `unpack_array`, `unpack_array_portable`, `unpack_from` |
| [`unstd.pack.ragged`](../src/unstd/pack/ragged.py) | `RaggedDecoder` |

The scalar functions, `Struct`, and `error` are the exact stdlib `struct`
objects. Compiled `Struct` instances keep their stdlib `format`, `size`,
`pack`, `pack_into`, `unpack`, `unpack_from`, and `iter_unpack` interfaces.

`pack_array` and `unpack_array` retain backend-dependent conversion and
array/view return behavior. The `pack` extra provides NumPy. The explicit
`pack_array_portable` and `unpack_array_portable` functions always use the
portable dtype subset and stdlib validation, returning bytes and an owned list.

`RaggedDecoder(dtype, *, columns, index_dtype="=i8", finite=False,
allow_empty=True)` compiles an integer-boundary table followed by numeric rows.
`unpack(buf, *, index_count)` validates geometry, boundaries, and the configured
finite-value constraint on every call. Exact bytes back immutable NumPy views;
other contiguous exporters snapshot before validation. Without NumPy, results
are tuple rows and integer boundaries. The optional native validator and stdlib
fallback enforce the same contract. Identity, publication limits, and
normalization remain with the caller.

`unpack_array` and `unpack_array_portable` accept keyword-only `offset=0` in
bytes and `count=-1` in elements.
The default count consumes the whole remainder; an explicit nonnegative count
selects a region without slicing and permits unrelated trailing bytes. Portable
decoding validates bounds and always returns independent Python scalars; NumPy
decoding retains views over the original buffer.

Portable packing can copy matching primitive storage from exact one-dimensional
`array.array` and NumPy arrays, including admitted strides and byte orders,
without boxing every scalar. Integer width/signedness and float64 must match;
float16/float32 conversions, subclasses and other iterables retain stdlib
validation. Output remains owned bytes. The
[packing guide](../src/unstd/pack/README.md) documents admission and fallback.

## Processes

Import `from unstd import proc`.

| Module | Exported API |
| --- | --- |
| [`unstd.proc`](../src/unstd/proc/__init__.py) | `DEFAULT_TIMEOUT`, `CalledProcessError`, `CompletedProcess`, `ProcessError`, `TimeoutExpired`, `capture`, `run`, `run_ok` |

`DEFAULT_TIMEOUT` is `60.0` seconds. `CalledProcessError` and `TimeoutExpired`
are the original stdlib exception classes. `run` returns `CompletedProcess`,
`run_ok` returns a boolean, and `capture` returns stripped stdout. Execution
continues to use explicit argument sequences, captured output, and finite
positive timeouts.

An optional bundled C backend handles admitted macOS process creation and
captured output without stdin on Linux/macOS. Linux keeps stdlib creation;
bounded waits use kernel exit notifications. Unsupported shapes retain stdlib
execution. The public
signatures, captured output, timeout exceptions and direct-child cleanup
contracts are unchanged. See the
[backend details and measurements](../src/unstd/proc/README.md#backend).

`CompletedProcess(argv, returncode, stdout, stderr, duration)` is a frozen,
slotted dataclass with these public instance members:

| Member | Meaning |
| --- | --- |
| `argv: tuple[str, ...]` | Executed arguments |
| `returncode: int` | Child exit status |
| `stdout: str` | Captured decoded standard output |
| `stderr: str` | Captured decoded standard error |
| `duration: float` | Elapsed execution time in seconds |
| `ok: bool` | Read-only property, true for exit status zero |

`ProcessError(result)` is a `CalledProcessError` subclass. Its `result` member
holds the `CompletedProcess`; inherited `returncode`, `cmd`, `output`, `stdout`,
and `stderr` remain available. Its string representation includes captured
stderr. Both result classes retain the public module identity `unstd.proc`,
including for class lookup and `CompletedProcess` pickles, while their
[definitions](../src/unstd/proc/results.py) live privately.

## Randomness

Import `from unstd import rand`; use `rand.crypto` for secure draws.

| Module | Supported API |
| --- | --- |
| [`unstd.rand`](../src/unstd/rand/__init__.py) | `Generator`, `choice`, `choices`, `crypto`, `floats`, `ints`, `randint`, `random`, `sample`, `seed`, `shuffle`, `uniform` |
| [`unstd.rand.draw`](../src/unstd/rand/draw.py) | `Generator`, `choice`, `choices`, `floats`, `ints`, `randint`, `random`, `sample`, `seed`, `shuffle`, `uniform` |
| [`unstd.rand.crypto`](../src/unstd/rand/crypto.py) | `below`, `bits`, `choice`, `token_bytes`, `token_hex`, `token_urlsafe` |

Module-level scalar calls use the shared stdlib PRNG; optional NumPy accelerates
bulk draws. The secure functions are the original `secrets` functions, with
`below` naming `secrets.randbelow` and `bits` naming `secrets.randbits`.
Seeding does not affect secure randomness.

`Generator` owns an independent stream:

```python
Generator(seed=None, *, backend="stdlib", bit_generator=None)
generator.backend -> Literal["stdlib", "numpy"]
generator.seed(n: int | None = None) -> None
generator.random() -> float
generator.randint(a: int, b: int) -> int
generator.uniform(a: float, b: float) -> float
generator.choice(seq: Sequence[T]) -> T
generator.shuffle(x: MutableSequence[T]) -> None
generator.sample(population: Sequence[T], k: int, *, counts=None) -> list[T]
generator.floats(n: int, *, low=0.0, high=1.0) -> FloatArray
generator.ints(n: int, low: int, high: int, *, endpoint=False, seeds=None) -> IntArray
generator.choices(seq: Sequence[T], n: int, *, weights=None) -> list[T]
```

`backend` is read-only. NumPy selection is explicit and requires the `rand`
extra. `bit_generator` accepts `PCG64`, `PCG64DXSM`, `SFC64`, or `Philox` when
using NumPy. Instance streams do not reseed module-level draws. `FloatArray`
and `IntArray` remain the existing return annotation aliases in `rand.draw`.

Module-level `ints` accepts the same `seeds=None` keyword. Supplying one unsigned
64-bit integer key per result selects the explicit **SplitMix64-v1** stream,
with effective inclusive output bounds in signed int64. This opt-in changes
values relative to constructing an MT19937 generator per key. It preserves each
key's value across reordered/chunked batches and leaves both ordinary engines
untouched; duplicate keys repeat results. Counts, keys and bounds are validated
even for empty batches. Return containers retain the selected backend's list or
int64 ndarray contract. Secure draws continue to use `rand.crypto`.

`Generator.shuffle` can use an optional CPython kernel for ordinary lists while
preserving exact stdlib permutations and subsequent RNG state. Custom methods,
engine hooks, sequence types and missing native support retain stdlib execution.
Module-level scalar aliases keep their direct stdlib bindings. The
[rand guide](../src/unstd/rand/README.md) covers these choices; the
[qualification](../bench/reports/rand/README.md) separates keyed count-phase
speedups from complete consumer costs.

## Regular expressions

Import `from unstd import rex`.

| Category | Exported API |
| --- | --- |
| Functions | `compile`, `compile_faithful`, `compile_linear`, `error`, `escape`, `findall`, `finditer`, `fullmatch`, `match`, `purge`, `search`, `split`, `sub`, `subn` |
| Types and policy exception | `LinearRegexError`, `Match`, `Pattern`, `RegexFlag` |
| Flags | `ASCII`, `DEBUG`, `DOTALL`, `IGNORECASE`, `LOCALE`, `MULTILINE`, `NOFLAG`, `UNICODE`, `VERBOSE`, `A`, `I`, `L`, `M`, `S`, `U`, `X` |

[`unstd.rex`](../src/unstd/rex/__init__.py) preserves the stdlib flag/type
objects, `error`, and `escape`. `compile` retains its documented native dialect
boundaries; `compile_faithful` uses Python semantics with fallback;
`compile_linear` requires native execution and raises `LinearRegexError` when
that guarantee cannot be met.

Ordinary compiled patterns expose `search`, `match`, `fullmatch`, `finditer`,
`findall`, `split`, `sub`, `subn`, and pattern metadata such as `pattern`,
`flags`, `groups`, and `groupindex`. Existing stdlib ancillary operations remain
available where the selected ordinary pattern supplies them. Match values
retain their documented stdlib/native projections and callback behavior.

Strict patterns returned by `compile_linear` support `search`, `match`, and
`fullmatch`, plus `pattern`, `flags`, `groups`, `groupindex`, and the read-only
`requires_ascii` property. Iteration, splitting, replacement, and scanner
operations are refused with `LinearRegexError`. No public constructor for the
private pattern adapter classes is added.

## Serialization

Import `from unstd.serde import jsonx, b64, ndjson, structs` as needed.

| Module | Exported API |
| --- | --- |
| [`unstd.serde.jsonx`](../src/unstd/serde/jsonx.py) | `JSONDecodeError`, `JSONDecoder`, `JSONEncoder`, `Json`, `canonical`, `canonical_strict`, `dump`, `dumpb`, `dumps`, `load`, `loads` |
| [`unstd.serde.b64`](../src/unstd/serde/b64.py) | `Error`, `b64decode`, `b64encode`, `decode`, `decode_file`, `decode_json`, `decode_text`, `decodebytes`, `encode`, `encode_file`, `encode_json`, `encodebytes`, `standard_b64decode`, `standard_b64encode`, `urlsafe_b64decode`, `urlsafe_b64encode` |
| [`unstd.serde.ndjson`](../src/unstd/serde/ndjson.py) | `NDJSONDecodeError`, `dump`, `dumpb`, `dumps`, `iter_loads`, `load`, `loads` |
| [`unstd.serde.structs`](../src/unstd/serde/structs.py) | `Codec`, `DecHook`, `EncHook`, `FloatHook`, `Order`, `float_enc_hook` |

`jsonx` retains stdlib JSON formatting, hooks, classes, and errors. `JSONEncoder`,
`JSONDecoder`, and `JSONDecodeError` are stdlib's original objects, including
their existing instance interfaces. `dumpb` adds compact UTF-8 bytes. The legacy
`canonical` format remains stable; `canonical_strict` provides its existing
restricted-value format. Neither format changes during module reorganization.

`b64.Error` is `binascii.Error`. Standard Base64 functions keep their documented
stdlib signatures and behavior. Convenience `encode`/`decode` keep their distinct
text, URL, padding, and validation defaults. `encode_file`/`decode_file` retain
the stdlib stream functions under the established non-conflicting names.

All six NDJSON read/write functions support the optional `codec=` argument.
Readers accept `skip_errors=False` and `on_error=None`. `NDJSONDecodeError`
instances expose `lineno` (the physical one-based line number) and `err` (the
original parsing or validation exception).

`structs` requires the `serde` extra. `Codec[T]` has this constructor:

```python
Codec(
    spec: type[T],
    *,
    enc_hook=None,
    dec_hook=None,
    order=None,
    decimal_format="string",
    uuid_format="canonical",
    strict=True,
    float_hook=None,
)
codec.encode(value: T) -> bytes
codec.decode(raw: bytes | str) -> T
codec.decode_or(raw: bytes | str, default: T | None = None) -> T | None
```

`encode` and `decode` are bound callable attributes backed by reusable msgspec
codecs. `decode_or` returns the default for msgspec decoding/validation failures
and malformed Unicode, with other exceptions propagating. `order` accepts
`None`, `"deterministic"`, or `"sorted"`; `decimal_format` accepts `"string"`,
`"number"`, or a callable over `Decimal`; `uuid_format` accepts `"canonical"`
or `"hex"`. `EncHook`, `DecHook`, `FloatHook`, and `Order` name the public
annotation types used by this constructor.

Untyped JSON, Base64, and NDJSON remain usable without the extra through their
stdlib paths.

## Text matching

Import `from unstd.text import fuzz`.

| Module | Exported API |
| --- | --- |
| [`unstd.text.fuzz`](../src/unstd/text/fuzz.py) | `best_match`, `cdist`, `difflib_ratio`, `distance`, `extract`, `indel_ratio`, `jaro_winkler`, `ratio` |

The `text` extra supplies RapidFuzz. The historical `ratio` keeps its existing
backend-selected metric. `indel_ratio` and `difflib_ratio` name explicit
algorithms. `best_match`, `extract`, and `cdist` retain `scorer=` selection and
the documented cutoff/tie behavior. Portable implementations remain available.

## Time

Import `from unstd.time import timeutil, zoned` as needed.

| Module | Exported API |
| --- | --- |
| [`unstd.time.timeutil`](../src/unstd/time/timeutil.py) | `Unit`, `combine`, `date_from_iso`, `date_today`, `datetime_today`, `elapsed_ms`, `epoch_ms`, `epoch_s`, `from_epoch_ms`, `from_epoch_s`, `from_iso`, `from_timestamp`, `gmtime`, `human`, `iso`, `localtime`, `mono`, `mono_ns`, `now`, `now_tz`, `parse_date`, `parse_iso`, `parse_utc`, `perf`, `perf_ns`, `process_time`, `process_time_ns`, `proto_timestamp`, `proto_timestamp_ms`, `sleep`, `span`, `strftime`, `strptime`, `thread_time`, `thread_time_ns`, `time_from_iso`, `today`, `utcnow`, `wall`, `wall_ns` |
| [`unstd.time.zoned`](../src/unstd/time/zoned.py) | `Disambiguate`, `Instant`, `PlainDateTime`, `RepeatedTime`, `SkippedTime`, `ZonedDateTime`, `instant`, `now`, `parse`, `to_utc`, `wall` |

`timeutil` uses stdlib time/datetime values. Clocks, `sleep`, native calendar
reads, conversion, formatting and assembly are direct native bindings. Thread
CPU clocks retain platform availability. `now()` and `from_timestamp(seconds)`
are local and naive by default; pass a zone for an aware result. `utcnow` is the
pre-bound UTC datetime call.

`from_iso`, `date_from_iso`, `time_from_iso`, and `strptime` retain native accepted
forms and exceptions. `parse_iso`, `parse_date`, and `parse_utc` return `None` for
invalid input; `parse_utc` also normalizes aware values to UTC and reads naive
values as UTC.

`Unit` is `"seconds" | "minutes" | "hours" | "days"`. Only the protobuf bridge
requires the `proto` extra, loaded on demand.

`zoned` requires the `time` extra. Its `Instant`, `PlainDateTime`, `ZonedDateTime`,
`RepeatedTime`, and `SkippedTime` names are the actual `whenever` classes and
exceptions, preserving their constructors and instance APIs. `now` is bound
from `ZonedDateTime.now`. `Disambiguate` is
`"compatible" | "raise" | "earlier" | "later"`; the wall-clock helpers preserve
their existing fold/gap policies and return types.

## TOML

Import `from unstd import toml`.

| Module | Exported API |
| --- | --- |
| [`unstd.toml`](../src/unstd/toml/__init__.py) | `TOMLDecodeError`, `aot`, `array`, `comment`, `document`, `dump`, `dumps`, `inline_table`, `item`, `load`, `loads`, `nl`, `parse`, `table` |

`load`, `loads`, and `TOMLDecodeError` are stdlib `tomllib` objects. Writing and
editable builders require the `toml` extra. `parse` and the builders remain
tomlkit's own objects; their editable document/node APIs are retained. `dump`
and `dumps` keep the existing common-dialect validation for plain data and
preserve metadata on tomlkit values. Missing write capabilities raise an
actionable `ImportError` while stdlib reads continue to work.

## YAML

Import `from unstd import yaml`.

| Module | Exported API |
| --- | --- |
| [`unstd.yaml`](../src/unstd/yaml/__init__.py) | `YAMLError`, `YamlScalar`, `YamlValue`, `dump`, `dump_all`, `dumps`, `load`, `load_all`, `loads`, `parse` |

The `yaml` extra provides both backend contracts. `yaml.pyyaml` and
`yaml.ruamel` are lazy public module bindings outside the wildcard exports.
Each is the original backend, retaining its loaders, dumpers, exceptions,
nodes and events. Accessing one does not import the other or the YAML 1.2 codec.

```python
from unstd.yaml import pyyaml as yaml

yaml.safe_load(raw)
```

Our document helpers use YAML 1.2 policy. `loads` and `load` read one safe
document; `load_all` reads lazily. `parse` preserves editable metadata; `dumps`,
`dump`, and `dump_all` retain it when writing. Duplicate-key and unknown-tag
rejection remain part of the parser policy. `YAMLError` is the backend exception
when installed, and retains the existing `ValueError` fallback when absent.

`YamlScalar` covers strings, integers, floats, booleans, bytes, dates, datetimes,
and `None`. `YamlValue` additionally covers the documented recursive mappings,
lists, tuples, and mutable sets. Both aliases retain their public `unstd.yaml`
identity even though validation and parser policy live in private modules.

## Compatibility paths and private implementation

Existing application imports continue to use their established module paths.
The public submodules listed above are retained, including direct imports from
`clone.structural`, `containers.sorted`, `containers.persistent`, `iters`'
combinator modules, `model.base`, `model.settings`, and `rand.draw`.

The following legacy availability attributes remain accessible and remain in
their existing `__all__` lists so wildcard imports keep working. They are
compatibility diagnostics rather than the supported capability API for new code:

| Module | Retained flag |
| --- | --- |
| `unstd.rand` | `HAVE_NUMPY` |
| `unstd.rand.draw` | `HAVE_NUMPY` |
| `unstd.containers.sorted` | `HAVE_SORTEDCONTAINERS` |
| `unstd.containers.persistent` | `HAVE_IMMUTABLES` |

Supporting modules have descriptive concern names; their location does not
add them to the supported API. The bundled extension
modules `unstd.serde._json_native`, `unstd.clone._clone_native`,
`unstd.crypto._stream_native`, `unstd.text._text_native`,
`unstd.compress._gzip_native`, `unstd.containers._sorted_native`,
`unstd.proc._proc_native`, and `unstd.pack._pack_native` retain their private
import names for build/runtime
compatibility. They are not user APIs, and users do not need to import them to
enable acceleration. Their presence may differ between builds.

Backend handles, native admission helpers, caches, parser subclasses, and other
underscore names are private. Moving their source does not introduce a public
`native` package or a new application configuration surface. Intentionally
public stdlib/backend classes and builders remain the original objects where
the existing API promises that identity.
