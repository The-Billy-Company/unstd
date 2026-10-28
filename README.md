---
doc_radar:
  sentinels:
    - file: pyproject.toml
      contains: ['version = "4.0.4"', 'requires-python = ">=3.12"']
    - file: CHANGELOG.md
      contains: ['## [4.0.4] - 2026-10-27']
---

# unstd

**An accelerated Python standard library for hot-path operations.**

unstd accelerates serialization, compression, deep copying, hashing, and other
hot paths with bundled native code, optional backends, and familiar Python APIs.

Requires **Python 3.12+**. The base install has **no third-party runtime
dependencies**.

## Install

```sh
python -m pip install unstd

# Add accelerated serialization and compression backends:
python -m pip install 'unstd[serde,compress]'
```

Install only the extras you need. Compatible CPython wheels include bundled C
extensions; source builds use them when a compiler is available and retain
portable fallbacks otherwise.

Linux CPython wheels also accelerate [`fs.atomic_write`](src/unstd/fs/README.md)
without an extra dependency, while retaining the complete Python fallback.

## Quick start

```python
from unstd import clone
from unstd.compress import gzipx
from unstd.serde import jsonx

payload = {"events": [{"name": "saved", "count": 1}]}

text = jsonx.dumps(payload)  # str; stdlib JSON defaults and formatting
raw = jsonx.dumpb(payload)  # bytes; compact UTF-8 JSON
copy = clone.deep(payload)  # independent mutable copy
packed = gzipx.compress(raw, mtime=0)
restored = jsonx.loads(gzipx.decompress(packed))
```

To migrate existing JSON calls, use `from unstd.serde import jsonx as json`.

For large JSON arrays, use `jsonx.iterload(source)` to process one record at a
time and `jsonx.dump_array(records, destination)` to export a generator with
bounded memory. See the [read guide](src/unstd/serde/README.md#stream-a-json-array),
[write guide](src/unstd/serde/README.md#write-a-json-array-from-a-generator), and
[read measurements](bench/reports/serde/json-array-stream.md) and
[write measurements](bench/reports/serde/json-array-write.md).

## Choose a module

| Operation | Module | Optional extra |
| --- | --- | --- |
| JSON, Base64, NDJSON, typed codecs | [`serde`](src/unstd/serde/README.md) | `serde` |
| Deep copying and dataclass conversion | [`clone`](src/unstd/clone/README.md) | None |
| Persistent JSON caching, atomic batches, TTLs, and parallel fills | [`cache`](src/unstd/cache/README.md) | None |
| Indexed JSON corpora, ordered selection, and bounded batches | [`dataset`](src/unstd/dataset/README.md) | None |
| Gzip/zlib streams, Zstandard, archive bindings | [`compress`](src/unstd/compress/README.md) | `compress`, `zstd`, `archives` |
| UUIDs and BLAKE3 hashing | [`ids`](src/unstd/ids/README.md), [`crypto`](src/unstd/crypto/README.md) | `ids`, `crypto` |
| Clocks, zoned time, Protobuf timestamp bridge | [`time`](src/unstd/time/README.md) | `time`, `proto` |
| Validated models and settings | [`model`](src/unstd/model/README.md) | `model` |
| Sorted collections and persistent mappings | [`containers`](src/unstd/containers/README.md) | `containers` |
| Regex and fuzzy matching | [`rex`](src/unstd/rex/README.md), [`text`](src/unstd/text/README.md) | `rex`, `text` |
| Numeric frames, bulk packing and random draws | [`pack`](src/unstd/pack/README.md), [`rand`](src/unstd/rand/README.md) | `pack`, `rand` |
| Array reductions, vector scoring and grouping | [`math`](src/unstd/math/README.md) | `math` |
| TOML writing and YAML parsing/editing | [`toml`](src/unstd/toml/README.md), [`yaml`](src/unstd/yaml/README.md) | `toml`, `yaml` |
| Audio decoding, encoding, and streaming | [`audio`](src/unstd/audio/README.md) | `audio` |
| Atomic files, subprocesses, iteration | [`fs`](src/unstd/fs/README.md), [`proc`](src/unstd/proc/README.md), [`iters`](src/unstd/iters/README.md) | None |

The [module index](src/unstd/README.md) and individual guides describe backend
requirements and fallback behavior.

## Compatibility and performance

Compatibility is defined per module. `jsonx` preserves stdlib JSON behavior,
including formatting, hooks, and errors, falling back when needed. Additional
helpers such as `clone.deep` and `proc.run` have their own documented contracts.
Gzip streams remain interoperable, but compressed bytes can differ by backend.

Without optional backends, modules use stdlib fallbacks where available;
capabilities without a faithful fallback raise `ImportError`. Some helpers,
including fuzzy matching and bulk randomness, can produce different results
across backends. Check the module guide before migrating.

Speed depends on your data, interpreter, platform, and installed backends.
Profile your application and benchmark its actual hot paths. The
[benchmark guide](bench/README.md) provides reproducible comparisons and reports
both gains and regressions.

## Development

See the [test guide](tests/README.md), [architecture](docs/architecture.md),
[API inventory](docs/api.md), and [security policy](SECURITY.md).

The [4.0.4 release notes](CHANGELOG.md#404---2026-10-27) cover the latest additions
and improvements. The [October 25 updates](docs/updates-2026-10-25.md) retain the
change index through version 4.0.2.

First-party code is Apache-2.0; bundled licenses are listed in [NOTICE](NOTICE).
