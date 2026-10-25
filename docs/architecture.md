---
doc_radar:
  sentinels:
    - file: ../src/unstd/yaml/__init__.py
      contains: ['from unstd.yaml import codec', 'globals()[name] = backend']
    - file: ../src/unstd/yaml/codec.py
      contains: ['def loads(', 'def dumps(']
    - file: ../src/unstd/time/timeutil.py
      contains: ['strftime = _time.strftime', 'combine = datetime.combine']
---

# Repository and package architecture

`unstd` keeps its existing public imports and optional-extra contracts. Directory
depth expresses ownership: public interfaces, private mechanisms, compiled
sources, tests, executable tooling and recorded evidence have distinct homes.

## Repository ownership

```text
README.md                         project entry point
pyproject.toml / uv.lock           package, extras and development environment
hatch_build.py                    optional native build integration
src/unstd/                       installable Python package and typing stubs
native/<package>/                domain-owned native source and provenance
docs/                            architecture and complete public API inventory
tests/
  suites/product/<package>/      contracts owned by a public package
  suites/assurance/               risks crossing package boundaries
  support/                       shared workers, fixtures and contract data
  tooling/                       standalone validation and campaign runners
bench/
  core/                          cases, timing and report machinery
  workloads/                     benchmark operation definitions
  profiles/                      complete function and application profiles
  diagnostics/                   focused backend and runtime investigations
  baselines/                     frozen performance floors
  reports/<domain>/              recorded measurements and explanatory reports
  audits/{math,crypto}/          retained audit programs and evidence
.github/workflows/               CI, campaigns and release automation
changelog.d/                     unreleased change fragments
```

Root license, notice, security policy and changelog files retain their standard
locations. Environments, caches and generated build outputs are ignored local
state. They are not source modules and are not part of the published package.

The existing test hierarchy already expresses package ownership and evidence
kind. Its inventory, marker selection and runner commands remain stable. Nested
guides explain the purpose of each first-party source and tooling directory.

## Python packages

All paths below are relative to `src/unstd/`. Public modules stay at their
existing paths. Supporting modules use descriptive concern names and remain implementation details.

| Package | Public interface | Private implementation boundary |
| --- | --- | --- |
| `audio` | `wavx.py` | `pcm.py` owns pure sample conversion and frame validation; backend state and file I/O remain in `wavx` |
| `clone` | Package exports and `structural.py` | Exact-type walker and optional private clone extension |
| `compress` | `gzipx.py` | `engines/native.py`, `engines/reader.py`, private native extension |
| `containers` | Package exports, `persistent.py`, `sorted.py` | Guarded imports and deferred missing-extra placeholders |
| `crypto` | `digest.py`, `tls.py`, `token.py` | `hashing.py` and private native stream extension |
| `fs` | Package functions | `durability.py` and `traversal.py`; atomic-write orchestration retains public sync and cleanup hooks |
| `ids` | `uid.py` | Private UUID fallback state stays with the generator |
| `iters` | Package functions and `consecutive.py`, `distinct.py`, `reshape.py`, `select.py` | Small focused combinator modules |
| `model` | Package classes, `base.py`, `settings.py` | Strict Pydantic model and settings configuration |
| `pack` | `binpack.py` | `dtypes.py` owns portable dtype grammar and bounded struct caches |
| `proc` | Package functions and classes | `results.py` implements result/error classes; execution stays at the public module |
| `rand` | Package functions, `draw.py`, `crypto.py` | Seeded engines remain separate from unseedable security draws |
| `rex` | Package functions, flags and types | `profile.py` proves semantic eligibility; `patterns.py` owns adapters; caches remain public-module state |
| `serde` | `jsonx.py`, `b64.py`, `ndjson.py`, `structs.py` | `encoding.py` and `recursion.py`; private native JSON extension |
| `text` | `fuzz.py` | Private native text extension; scorer dispatch remains with public calls |
| `time` | `timeutil.py`, `zoned.py` | Native clock/calendar bindings, instant arithmetic and optional zoned-time backend stay separate |
| `toml` | Package format functions and builders | `dialect.py` normalizes plain data to the shared TOML dialect |
| `yaml` | Lazy backend modules and YAML 1.2 helpers | `codec.py` owns document functions and value validation; `parser.py` owns safe parser policy |

The package root and compression facade retain the rule's documented
language-package-root import shelf, avoiding one-file wrapper packages. Other
domain source folders are flat code leaves. Support modules declare an empty
`__all__`, preserving the public operation inventory. Historical regex pickle
identities remain registered without a duplicate compatibility source file.

Small modules stay focused rather than gaining empty wrapper layers. Public
entrypoints retain their original definitions when they own backend switches,
caches or injectable state. This preserves the behavior of our fault, concurrency
and fallback tests, as well as signatures and hot-call dispatch.

## Native implementation boundary

```text
native/
  audio/                         reviewed external libsndfile build policy
  clone/src/                     structural cloning kernel
  crypto/src/                    streaming digest/token kernel
  rand/src/                      stdlib-faithful shuffle and explicit keyed integers
  serde/src/                     JSON kernel and private headers
  text/
    src/                         default C kernel
    rust/                        optional Rust prototype and CPython adapter
  compress/
    src/                         gzip adapters and stream/inflater code
    vendor/{libdeflate,zlib}/     authenticated upstream sources and licenses
    experiments/                 qualification tools and retained evidence
```

Builds read native sources from this tree and emit the same underscored binary
modules as before: `unstd.serde._json_native`, `unstd.clone._clone_native`,
`unstd.crypto._stream_native`, `unstd.text._text_native` and
`unstd.compress._gzip_native`. Rand also builds `unstd.rand._rand_native` for
compatible shuffling and explicit keyed batches. Their typing stubs retain the same paths.
Wheels contain Python interfaces and optional binaries; native source belongs
in the source distribution. Vendored source contents, licenses and authenticated
manifests retain their meaning after relocation.

These modules are private. Users call the documented Python APIs; builds and
internal qualification tools may inspect the binary bindings. Python privacy is
a naming and support boundary, not an access-control mechanism.

Rand's default streams retain exact engine output/state. The explicit
`ints(..., seeds=...)` path instead uses versioned, stateless SplitMix64 values;
its native and Python implementations agree. Its qualified 100× gains apply
to batched per-key counts, while complete consumer timings remain separately
reported. See the [rand evidence](../bench/reports/rand/README.md).

## Compatibility and verification

The [API inventory](api.md) records supported names and retained compatibility
attributes. No consumer import rewrite is needed. Existing upstream class
aliases remain the original objects, including sorted containers, time types,
JSON codec classes, regex types and TOML builders. Exported process classes keep
their historical `unstd.proc` identity for pickling and inspection.

The [public API contract](../tests/support/packaging/public_api.json) captures the
44 pre-reorganization modules and 275 declared exports. Its
[gate](../tests/suites/assurance/gates/packaging/test_public_api.py) checks export
lists, package-owned callable signatures, module identity and process-result
pickling. It preserves legacy availability flags, including wildcard imports;
these flags remain compatibility details rather than recommended user APIs.

Existing benchmark module commands forward to their new owners while retaining
module identity for diagnostic injection. Recorded JSON evidence, source
snapshots and frozen baselines retain their bytes. Profilers include nested
private implementations and continue supporting saved older comparison trees.

Validation covers product contracts, optional backends, native builds, wheel and
source-distribution installs, provenance checks, benchmark tools, lint, types
and security analysis. Performance floors remain evidence gates; a layout
change does not justify resetting them.
