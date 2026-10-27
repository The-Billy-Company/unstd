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

All paths below are relative to `src/unstd/`. Public module paths remain
stable. Each domain owns its code and a guide with an exact file map and test
command. Small domains are flat code leaves; descriptive filenames express
responsibility without an extra helper package.

| Package | Public interface | Supporting concerns |
| --- | --- | --- |
| `audio` | `wavx.py` | `pcm.py` owns sample conversion and frame validation. |
| `cache` | Package class `Cache` | Short SQLite WAL transactions own persistence, consistent batch snapshots, expiry and FIFO retention; `locking.py` prepares canonical identities once and owns bounded key locks/fork cleanup; `serde.jsonx` owns default JSON encoding and structural codecs admit typed artifacts. |
| `clone` | Package exports, `structural.py` | Graph walking and optional native cloning. |
| `compress` | `gzipx.py`, `zlibx.py`, original codec/archive ports | `engines/` owns provider admission, gzip reading, flush validation and import identity; `libarchive/` owns its binding and type surface. |
| `containers` | Lazy package exports, `persistent.py`, `sorted.py` | `ordering.py` owns compatible accelerated collection classes. |
| `crypto` | `digest.py`, `tls.py`, `token.py` | `hashing.py` owns BLAKE3 admission and worker policy. |
| `dataset` | Package class `Dataset` and function `build` | `reader.py` owns sequence and batch semantics; `storage.py` owns portable indexed JSON files, atomic construction, mapping, and integrity; the optional native kernel copies checked row ranges. |
| `fs` | Package functions | `durability.py` and `traversal.py`; public orchestration owns Linux native admission and injectable sync/cleanup hooks. |
| `ids` | `uid.py` | UUID generation and fallback state stay together. |
| `iters` | Package functions | `consecutive.py`, `distinct.py`, `reshape.py`, `select.py` own focused combinators. |
| `math` | Package functions | Shape/layout dispatch between checked portable C buffer kernels and NumPy reductions; the optional `math` extra supplies NumPy without adding a C ABI dependency. |
| `model` | Package classes, `base.py`, `settings.py` | Strict model and settings configuration. |
| `pack` | `binpack.py`, `ragged.py` | `dtypes.py` owns portable dtype grammar and caches; `buffers.py` owns bounded buffer admission; ragged layouts own immutable frames and optional fused native validation. |
| `proc` | Package functions and classes | `backend.py` owns spawning/capture; `results.py` owns results and errors. |
| `rand` | Package functions, `draw.py`, `crypto.py` | `keyed.py` owns keyed deterministic integer batches; secure draws remain separate. |
| `rex` | Package functions, flags and types | `profile.py` proves semantic eligibility; `patterns.py` owns adapters; public caches stay at the entrypoint. |
| `serde` | `jsonx.py`, `b64.py`, `ndjson.py`, `structs.py` | `encoding.py` owns JSON eligibility/encoding; `recursion.py` preserves interpreter stack budgets; `_array_writer.py` batches array writes. |
| `text` | `fuzz.py` | Scorer admission and native dispatch stay with the calls. |
| `time` | `timeutil.py`, `zoned.py` | Native calendar bindings, instant arithmetic and zoned-time backend remain distinct. |
| `toml` | Package format functions and builders | `dialect.py` normalizes data to the shared TOML dialect. |
| `yaml` | Lazy package functions, original backend modules | `codec.py` owns document operations/value policy; `parser.py` owns safe parsing and emitting. |

`extras.py` owns the shared deferred `ImportError` placeholders. The package
root and compression facade use the restructure rule's language-package-root
utility shelf: public import files may sit beside concern packages when making
each tiny module a package would only add a shallow boundary. All other domain
source folders are code leaves. Interpreter initializers, native binaries and
native typing stubs retain their required names; Python implementation files
use descriptive names without a leading underscore.
Support modules declare an empty `__all__`, so naming clarity does not expand
the public operation inventory or its performance workload obligations.

Public entrypoints retain definitions that own backend switches, caches or
injectable state. Source moves preserve algorithms, signatures and hot-call
dispatch. The historical `unstd.rex._faithful` module identity remains registered
for serialized exception/pattern compatibility, without a duplicate source file.

## Native implementation boundary

```text
native/
  audio/                         reviewed external libsndfile build policy
  clone/src/                     structural cloning kernel
  containers/src/                admitted sorted-container query kernels
  crypto/src/                    streaming digest/token kernel
  fs/
    src/                         Linux atomic-write kernel
    qualification/               test-only syscall fault/signal observer
    experiments/                 historical standalone profiling proof
  serde/src/                     JSON kernel and private headers
  proc/src/                      POSIX spawning and captured-pipe kernels
  pack/src/                      immutable numeric-frame and finite-value validation
  rand/src/                      stdlib-faithful shuffle and explicit keyed integers
  text/
    src/                         default C kernel
    rust/                        optional Rust prototype and CPython adapter
  compress/
    src/                         gzip adapters and stream/inflater code
    vendor/{libdeflate,zlib,isa-l}/ authenticated upstream sources and licenses
    experiments/                 qualification tools and retained evidence
```

Builds read native sources from this tree and emit private binary modules,
including `unstd.serde._json_native`, `unstd.clone._clone_native`,
`unstd.crypto._stream_native`, `unstd.text._text_native`,
`unstd.compress._gzip_native`, `unstd.containers._sorted_native`,
`unstd.proc._proc_native`, `unstd.rand._rand_native`, and
`unstd.pack._pack_native`, and `unstd.dataset._dataset_native`.
Linux additionally builds `unstd.fs._fs_native`.
The [build registry](../hatch_build.py) lists all bundled extensions; their typing
stubs live beside the Python interfaces.
Wheels contain Python interfaces and optional binaries; native source belongs
in the source distribution. Vendored source contents, licenses and authenticated
manifests retain their meaning after relocation.

These modules are private. Users call the documented Python APIs; builds and
internal qualification tools may inspect the binary bindings. Python privacy is
a naming and support boundary, not an access-control mechanism.

Filesystem dispatch admits exact bytes and default UTF-8 text with an existing
parent only when its OS/path/orchestration bindings retain their original
identity. Unsupported inputs and missing native capabilities select the complete
Python implementation before scratch creation; a native I/O exception never
retries the write. Streaming `atomic_writer` remains Python. The kernel records
descriptor ownership before checking signals and successful rename before
reporting post-commit cancellation. The [filesystem source guide](../native/fs/README.md)
maps production and test-only sources; its standalone experiment is excluded
from package builds.

## Cross-platform performance

Linux and macOS share the first-party JSON, clone, text and crypto kernels.
They also share admitted sorted-container and rand kernels. Process creation
uses the native route on macOS and stdlib on Linux; captured text pipes can use
the native poll/read loop on both. Filesystem native atomic writes are
Linux-only. These kernels add no third-party runtime dependency; sorted
containers retain their existing optional backend requirements.
Acceleration still depends on the installed extras, successful native builds,
CPython version and build configuration. Compare matching dependency versions
and ordinary GIL-enabled release interpreters; compiler-free source installs
and free-threaded interpreters can select portable fallbacks. Native builds
retain the selected Python toolchain's compiler and optimization settings.

Atomic-write acceleration is Linux-only and needs no extra. Its
[installed-package qualification](../bench/reports/fs/production/README.md)
checks native and forced-Python routes from direct wheels and sdist rebuilds,
including cancellation, cleanup/error parity and 13 consumer workload shapes.
Small-write CPU savings and variable disk wall time are recorded separately;
the Linux arm64 measurements do not establish a speedup on every storage device.

Gzip streaming is an intentional exception: macOS uses platform libz through
the bundled adapter, while Linux selects zlib-ng's SIMD inflater. The private
Linux adapter still compiles the authenticated zlib closure. Both public paths
reuse the running interpreter's gzip framing and bounded read implementation;
libdeflate supplies native CRC32. The bundled inflater has a per-object lock
and releases the GIL for native calls with at least 32 KiB of output space.
The retained [qualification reports](../bench/reports/compress/README.md)
record the active decoder, independent-stream throughput and paired
consumer timings on both machines.
Mac ARM can select an already-loaded zlib-ng provider after a bounded probe of
eligible repetitive streams; ordinary streams retain Apple libz. Qualified
Linux x86_64 builds can additionally assemble the private ISA-L decoder with
NASM/Yasm and admit it through the bundled bounded bridge. This is a build- and
input-dependent capability, not a universal stream route or speed guarantee.
Eligible complete buffers use libdeflate on both platforms, with its supported
CPU-specific instruction dispatch. BLAKE3 worker counts respect process CPU
availability, including Linux affinity on Python 3.12, and are sampled at import.

Rand's default streams retain exact engine output/state. The explicit
`ints(..., seeds=...)` path instead uses versioned, stateless SplitMix64 values;
its native and Python implementations agree. Its qualified 100× gains apply
to batched per-key counts, while complete consumer timings remain separately
reported. See the [rand evidence](../bench/reports/rand/README.md).

Compression module ports expose the actual stdlib or optional provider module,
preserving its classes, native contexts and qualified imports. `zlibx` retains
the running interpreter's incremental decompressor because optional backends
can differ in EOF and flush state. `unstd.compress.zlib` retains stock compressed
bytes for persisted-hash contracts. The [port matrix](../bench/reports/compress/port-contracts.md)
documents the boundary between codec behavior and application-owned policy.

The [performance workflow](../.github/workflows/performance.yml) builds both
revisions independently on Linux and macOS with Python 3.13 and 3.14, then
compares them against a baseline measured on the same worker with identical
dependencies and workloads. This includes the distinct Python 3.14 C fast paths.
It retains separate platform evidence and also runs when native sources change.
JSON qualification additionally exercises actual installed native kernels and
their public bindings, then gates paired record timings at a 15% maximum
slowdown. Release wheels run the same native check before their full contracts.
These checks detect regressions on each platform; they cannot guarantee equal
elapsed times across machines. CPU architecture, memory bandwidth, scheduling,
storage and OS services still affect throughput and latency. Use the
[benchmark harness](../bench/README.md) on the actual deployment host to measure
those differences before making an absolute speed claim.

## Compatibility and verification

The [API inventory](api.md) records supported names and retained compatibility
attributes. No consumer import rewrite is needed. Existing upstream class
aliases remain the original objects, including persistent maps, time types,
JSON codec classes, regex types and TOML builders. Sorted containers use compatible
subclasses when their bundled CPython accelerator is available. Exported process classes keep
their historical `unstd.proc` identity for pickling and inspection.

The [public API contract](../tests/support/packaging/public_api.json) captures the
public modules and their declared exports. Its
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
