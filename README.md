# unstd

**An accelerated Python standard library for hot-path operations.**

unstd speeds up serialization, compression, deep copying, hashing, and other
operations that dominate busy Python services. It combines bundled native code
and optional accelerated backends with familiar Python APIs and focused utility
helpers.

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

## Choose a module

| Operation | Module | Optional extra |
| --- | --- | --- |
| JSON, Base64, NDJSON, typed codecs | [serde](src/unstd/serde/README.md) | `serde` |
| Deep copying and dataclass conversion | [clone](src/unstd/clone/README.md) | None |
| Gzip compression and streaming | [compress](src/unstd/compress/README.md) | `compress` |
| UUIDs and BLAKE3 hashing | [ids](src/unstd/ids/README.md), [crypto](src/unstd/crypto/README.md) | `ids`, `crypto` |
| Regex and fuzzy matching | [rex](src/unstd/rex/README.md), [text](src/unstd/text/README.md) | `rex`, `text` |
| Bulk packing and random draws | [pack](src/unstd/pack/README.md), [rand](src/unstd/rand/README.md) | `pack`, `rand` |
| Atomic files, subprocesses, iteration | [fs](src/unstd/fs/README.md), [proc](src/unstd/proc/README.md), [iters](src/unstd/iters/README.md) | None |

The [module index](src/unstd/README.md) also covers containers, time, models,
TOML, YAML, audio, and their backend requirements.

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

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for licensing details.
