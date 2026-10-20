# unstd

stdlib-faithful replacements, with optional native acceleration where it
preserves the contract. Modules follow the stdlib call surface, so migrating a
call site is an import swap. JSON proves native eligibility and falls back to
stdlib codecs to preserve formatting, values, callbacks and errors.

```python
import json  # →
from unstd.serde import jsonx  # faithful json API; dumpb for compact transport
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

CPython platform wheels include an optional C guard for fast, compatible JSON
encoding. Source builds use it when a compiler is available and otherwise keep
the portable fallback. The runtime dependency set and public APIs are unchanged.
See the [Billy fixture measurements](bench/billy-serde-optimization-results.md)
for release comparisons, including remaining overhead relative to 2.1.1.

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
| [`yaml`](src/unstd/yaml/README.md) | `unstd[yaml]` | ruamel.yaml: safe YAML 1.2 and comment-preserving edits (required) |
| [`compress`](src/unstd/compress/README.md) | `unstd[compress]` | zlib-ng → stdlib `gzip`, with faithful file framing and errors |
| [`proc`](src/unstd/proc/README.md) | — | stdlib `subprocess` |
| [`fs`](src/unstd/fs/README.md) | — | stdlib `os` |
| [`iters`](src/unstd/iters/README.md) | — | stdlib `itertools` |

`clone`, `proc`, `fs`, and `iters` ship in the base install.

## Testing

The [test suite](tests/README.md) follows Billy's owner-first Python layout:
product contracts, cross-cutting assurance, shared support, and standalone tools.
The [P0 boundary suites](tests/P0.md) exercise installed packaging, real TLS peers,
filesystem/process failures, and parser/token security. The
[coverage plan](tests/COVERAGE.md) maps parity, properties, fuzzing,
penetration, fault injection, packaging, concurrency, resource, and performance
obligations. Inspect implemented and planned lanes with
`uv run --no-sync python -m tests.tooling list`; run the active suite with
`uv run --no-sync pytest -q`.

The [fuzz runner](tests/tooling/fuzz/README.md) supplies eleven parser/token
targets, authenticated corpora, deterministic mutation, and bounded Atheris
campaigns. Run `uv run --no-sync python -m tests.tooling.fuzz check` to validate
the inventory, or `uv run --no-sync python -m tests.tooling.fuzz replay` to
replay every installed target. CI retains findings and coverage corpora.

The [chaos runner](tests/tooling/chaos/README.md) exercises recorded filesystem,
process, stream and clock/entropy failures and recovery. Run
`uv run --no-sync python -m tests.tooling.chaos run --seed 173` for a bounded
installed-backend campaign with per-case journals and reproducible schedules.

The [concurrency runner](tests/tooling/concurrency/README.md) checks atomic file
histories, UUID contention and fork safety, and shared cache/parser isolation.
Run `uv run --no-sync python -m tests.tooling.concurrency run --seed 173` for
bounded installed-backend workers with retained histories and replay.

## Prior art

- **orjson** (Kloss) / **msgspec** (Fitzgerald) / **pybase64** (aklomp libbase64) — the serde backends.
- The guarded-optional-backend pattern is the one uvloop popularized: import the accelerator, fall back when it is absent.

## License

Apache-2.0
