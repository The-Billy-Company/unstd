# unstd

Stdlib-compatible codecs and focused utility APIs, with optional native
acceleration. Documented stdlib replacements preserve their call surface;
additional helpers have their own contracts. JSON proves native eligibility
and falls back to stdlib codecs to preserve formatting, values, callbacks and
errors.

```python
import json  # →
from unstd.serde import jsonx  # faithful json API; dumpb for compact transport
```

The base install adds zero third-party runtime dependencies. External accelerated
backends ride optional extras with guarded imports that fall back to the stdlib
(or raise a clear `ImportError` where
no fallback is faithful). Install `unstd` and you pay nothing. Install
`unstd[serde]` and the serde modules light up.

## Install

```console
python3 -m pip install unstd
python3 -m pip install 'unstd[serde,ids,crypto]'
```

CPython platform wheels include optional C acceleration for compatible JSON,
exact builtin structural cloning, and exact-string Levenshtein/Jaro scoring.
Source builds use these modules when a compiler is available and otherwise keep
the portable fallback. The runtime dependency set and public APIs are unchanged.
See the [Billy fixture measurements](bench/billy-serde-optimization-results.md)
for release comparisons, including remaining overhead relative to 2.1.1.
The [native kernel audit](bench/native-results.md) records clone and text
measurements and compatibility boundaries.

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
| [`clone`](src/unstd/clone/README.md) | — | bundled C → Python exact-type walk, `copy.deepcopy` per foreign node |
| [`text`](src/unstd/text/README.md) | `unstd[text]` | rapidfuzz → stdlib `difflib` |
| [`audio`](src/unstd/audio/README.md) | `unstd[audio]` | soundfile + numpy → stdlib `wave` |
| [`toml`](src/unstd/toml/README.md) | `unstd[toml]` | read is `tomllib`; write requires tomlkit |
| [`yaml`](src/unstd/yaml/README.md) | `unstd[yaml]` | ruamel.yaml: safe YAML 1.2 and comment-preserving edits (required) |
| [`compress`](src/unstd/compress/README.md) | `unstd[compress]` | zlib-ng → stdlib `gzip`, with faithful file framing and errors |
| [`proc`](src/unstd/proc/README.md) | — | stdlib `subprocess` |
| [`fs`](src/unstd/fs/README.md) | — | stdlib `os` |
| [`iters`](src/unstd/iters/README.md) | — | stdlib `itertools` |

`clone`, `proc`, `fs`, and `iters` ship in the base install.

Check the module contract when migrating: `clone.deep` intentionally duplicates
shared references, text similarity uses different algorithms across backends,
and random bulk draws can return arrays or lists with different seeded streams.
These helpers extend the stdlib rather than promising an import-only migration.

Opt into explicit contracts when results or guarantees must survive backend
changes. Existing defaults and persisted canonical bytes remain unchanged:

- [Text](src/unstd/text/README.md): `indel_ratio` or `difflib_ratio`, selected
  through `scorer=` on matching helpers, keep the algorithm explicit.
- [Packing](src/unstd/pack/README.md): `pack_array_portable` and
  `unpack_array_portable` use struct validation and owned Python results.
- [Randomness](src/unstd/rand/README.md): `Generator(seed, backend="stdlib")`
  owns an independent stream and keeps stdlib semantics with NumPy installed.
- [Identity](src/unstd/serde/README.md): `canonical_strict` rejects unsupported
  identity values instead of converting them implicitly.
- [Regex](src/unstd/rex/README.md): `compile_faithful` preserves Python matching
  semantics; `compile_linear` guarantees a single `search`, `match`, or
  `fullmatch` and refuses fallback and iterative operations.
- [Filesystem](src/unstd/fs/README.md): `durable="strict"` and
  `sync_dir(..., strict=True)` report directory-sync failures.

## Repository layout

- [`src/`](src/README.md) contains the installable Python package and bundled C
  extensions; [`src/unstd/`](src/unstd/README.md) indexes the module APIs.
- [`tests/`](tests/README.md) contains product and assurance suites, shared
  support, and standalone campaign tools.
- [`bench/`](bench/README.md) contains benchmark drivers and recorded results.
- [`native/`](native/README.md) contains the opt-in Rust text experiment.
- [`.github/`](.github/README.md) contains CI, campaign, audit, and release workflows.
- [`changelog.d/`](changelog.d/README.md) contains unreleased change fragments.

Each repository folder has a README describing its contents and role. Keep that
README current when adding or moving files, and include one with new folders.
Generated environments, caches, and build outputs are excluded from this policy.

## Security and shared logic

The [static analysis policy](SECURITY.md) documents required SAST and duplication
checks, local commands, reviewed exceptions, and failure handling. CI and release
both require clean security analysis and production duplicate-code checks.

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

The quality gate runs `uv run --no-sync ruff check .`,
`uv run --no-sync ruff format --check .`, and `uv run --no-sync mypy`.
CI checks Python 3.12–3.14 type contracts for Linux, macOS, and Windows.
Production and benchmark code require complete annotations; mypy also rejects
missing type information, unsafe overrides, unused awaitables, possibly
uninitialized values, and incomplete pattern matches. Development stubs cover
protobuf and sorted containers without adding runtime dependencies. Compatible
exceptions for dynamic stdlib APIs, private regex internals, and deliberately
invalid test inputs are documented in `pyproject.toml`. The
[typing gate tests](tests/suites/assurance/gates/test_typing.py)
verify that the actual analyzers accept valid code and reject seeded mistakes.

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
