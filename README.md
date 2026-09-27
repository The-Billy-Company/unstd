# unstd

stdlib-faithful accelerated replacements. Each module wraps a native codec
behind the call surface of the stdlib module it stands in for, so migrating a
call site is an import swap, not a rewrite.

```python
import json  # →
from unstd.serde import jsonx  # jsonx.dumps / jsonx.loads, same surface, faster
```

The base install is pure stdlib: depending on `unstd` adds zero third-party
closure. Every accelerated backend rides an optional extra with a guarded
import that falls back to the stdlib (or raises a clear `ImportError` where
no fallback is faithful). Install `unstd` and you pay nothing. Install
`unstd[serde]` and the serde modules light up.

## Install

```console
python3 -m pip install unstd
python3 -m pip install 'unstd[serde,ids,crypto]'
```

## Groups

| Group | Extra | Backend / fallback |
|---|---|---|
| [`serde`](src/unstd/serde/README.md) | `unstd[serde]` | orjson / msgspec / pybase64 → stdlib `json`/`base64` |
| [`ids`](src/unstd/ids/README.md) | `unstd[ids]` | uuid-utils → `uid` falls back to stdlib UUIDv7 |
| [`crypto`](src/unstd/crypto/README.md) | `unstd[crypto]` | blake3 (required; hashlib ships BLAKE2, a different algorithm) |
| [`time`](src/unstd/time/README.md) | `unstd[time]` | `timeutil` is pure stdlib; `zoned` requires `whenever`, and `timeutil`'s protobuf `Timestamp` bridge requires `unstd[proto]` |
| [`model`](src/unstd/model/README.md) | `unstd[model]` | pydantic (required) |
| [`rex`](src/unstd/rex/README.md) | `unstd[rex]` | irregex → stdlib `re` |
| [`containers`](src/unstd/containers/README.md) | `unstd[containers]` | sortedcontainers + immutables (required) |
| [`rand`](src/unstd/rand/README.md) | `unstd[rand]` | numpy PCG64 → stdlib `random`; `crypto` is always `secrets` |
| [`pack`](src/unstd/pack/README.md) | `unstd[pack]` | numpy → stdlib `struct` |
| [`clone`](src/unstd/clone/README.md) | — | stdlib; exact-type walk, `copy.deepcopy` per foreign node |
| [`text`](src/unstd/text/README.md) | `unstd[text]` | rapidfuzz → stdlib `difflib` |
| [`audio`](src/unstd/audio/README.md) | `unstd[audio]` | soundfile + numpy → stdlib `wave` |
| [`toml`](src/unstd/toml/README.md) | `unstd[toml]` | read is `tomllib`; write requires tomlkit |
| [`proc`](src/unstd/proc/README.md) | — | stdlib `subprocess` |
| [`fs`](src/unstd/fs/README.md) | — | stdlib `os` |
| [`iters`](src/unstd/iters/README.md) | — | stdlib `itertools` |

`clone`, `proc`, `fs`, and `iters` ship in the base install.

## Prior art

- **orjson** (Kloss) / **msgspec** (Fitzgerald) / **pybase64** (aklomp libbase64) — the serde backends.
- The guarded-optional-backend pattern is the one uvloop popularized: import the accelerator, fall back when it is absent.

## License

Apache-2.0
