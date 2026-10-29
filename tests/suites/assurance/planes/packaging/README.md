# Installed packaging contracts

Linux wheels additionally exercise the packaged native filesystem writer
through public `fs.atomic_write`, then select its Python fallback in the same
pristine consumer. The native probe observes accepted strict byte publication
through the real installed kernel, then verifies a text overwrite with
`fs._native=None`. Non-Linux builds exclude that accelerator; free-threaded
builds retain Python. Source archives retain its production C entrypoint
`native/fs/src/adapter.c` independently of the historical experiment.

This lane freezes the working tree's release inputs into an external session
snapshot, including new/untracked package sources, before reading its oracles or
building real artifacts. This keeps concurrent edits from changing inputs between
the two build routes. It builds a wheel and sdist through isolated PEP 517 builds,
then builds another wheel from that sdist. Missing build tools, uncached build
requirements, subprocess failures and deadlines are failures; no artifact test
quietly skips its prerequisite.

```console
uv run --no-sync python -m tests.tooling run packaging -- -q
uv run --no-sync pytest -q tests/suites/assurance/planes/packaging
uv run --no-sync pytest -q --require-extras tests/suites/assurance/planes/packaging
```

The host needs `uv`, the project's dev environment, and cached Hatchling and
setuptools build dependencies. Default native builds retain the C kernels and do
not require Cargo. Strict Linux x86_64 builds need Yasm >=1.3 or NASM >=2.11.01
for ISA-L, including the base-install packaging lane; CI provisions Yasm before
these builds. The opt-in `UNSTD_TEXT_RUST=1` text experiment needs a stable
Rust/Cargo toolchain; it has no crate dependencies and builds with `--locked --offline`.
Prepare Python build dependencies using the project's two-day quarantine before
running this offline lane. Build commands explicitly use `--offline`, isolated
build environments, no automatic interpreter downloads and `--exclude-newer P2D`.
Per-extra exports and installs copy quarantine settings from the frozen project
manifest, including all per-package exceptions, rather than duplicating policy.
They never fall back to the network or an unisolated builder. Each command has a
90-second ceiling; product consumer processes have a 30-second ceiling.
For the required per-extra runtime matrix, prepare the locked artifact cache with
`uv sync --locked --all-extras --group dev`, then select `--require-extras`.
Without that flag the optional-backend consumers report an explicit skip; their
tests are implemented and become mandatory in the required-extra run. No missing
cache, backend, resolver, import or native library failure is skipped there.

## Evidence

- Both wheel routes contain every package source/data file from the build
  snapshot, the typing marker and exact license/notice bytes. Tests and benchmark
  tools stay out of the installed wheel. Wheel RECORD digests and sizes are
  independently recomputed for every member, and paths, duplicate members,
  native CPython or portable wheel tags, metadata, supported Python and
  license metadata are checked. An explicit compiler failure verifies the
  portable fallback; native release builds require the extension to compile.
  For fs, both a missing compiler and failure of only its C source are followed
  by an isolated installed-consumer write with recursive parent creation and
  scratch cleanup. A failure of only fs preserves the other native modules;
  the same source failure must reject a strict release build without a wheel.
  In opted-in Rust builds, missing Cargo and invalid Rust sources independently
  disable only the text extension, clear stale editable text binaries, and fail
  required native builds. C compilation failures remain independent.
- The sdist preserves all build inputs and package files, excludes local
  environments and caches, has safe non-link members, and reconstructs the
  entire wheel payload, including dependency metadata and RECORD.
  Rust sources, Cargo manifest and lockfile are included; Cargo `target/` outputs
  stay outside both artifacts. Wheels contain the linked extension without a
  separate Rust runtime or any new Python dependency.
- Every advertised extra has its own metadata assertion, including empty
  `clone`; every requirement is guarded by exactly one extra, proving the base
  metadata has no unconditional dependency.
- Required-extra runs also exercise each extra alone for each wheel route.
  `uv export --locked --no-dev --no-default-groups --no-emit-project --extra EXTRA --format pylock.toml`
  exports that extra's exact closure from the frozen lockfile, retaining artifact
  URLs and hashes. Direct URLs avoid depending on registry metadata that a locked
  sync does not need to cache.
  A new unseeded consumer receives only the product wheel and that closure, with
  offline hash verification. Source-only dependencies can reuse cached builds or
  build in isolation from cached prerequisites under the same two-day quarantine;
  no dependency preparation fetches from the network during pytest.
  Its exact distribution/version map must
  equal the export, including transitives. Each direct backend must originate
  inside that environment; unrelated backends must be absent unless shared by
  the exported closure. Empty `clone` receives no dependency.
  Real public calls prove native execution through native types, exact backend
  bindings or observed delegation to real backend functions. The probes cover
  orjson, pybase64, msgspec, UUIDs, BLAKE3/tokens, zoned time, protobuf, models and
  settings, regex, sorted/persistent containers, NumPy random/packing, rapidfuzz,
  soundfile/WAV, TOML, YAML and gzip/zlib-ng. Base consumers remain in their own
  pristine environments throughout.
  Compression extras also verify native Zstandard dictionaries, frame bounds and
  reader ownership, plus real libarchive gzip decoding with the original reader,
  FFI and error module identities. The `archives` extra installs the Python
  binding on Linux/macOS; a separately supplied native libarchive library is
  required for its runtime consumers. CI provisions it on Linux. Missing native
  libraries have separate explicit-import failure tests; base installs never
  load one.
  Qualified ZIP and Zstandard child imports must preserve their canonical
  modules, classes, error catchability and import metadata in installed wheels.
  Gzip consumers also execute the installed C inflater's bounded direct-output
  path and public stream reader against a highly compressed payload, preserving
  its following-member bytes and native backend identity. The source archive
  explicitly retains the inflater's module-state and shape-probe headers.
- A separate unseeded virtualenv for each wheel installs that wheel offline with
  `--no-deps`. Every product probe runs from an external consumer directory in a
  new `python -I` process. The probe verifies the installed distribution set is
  exactly `{unstd}`, every optional backend is absent, the product origin is
  inside the consumer environment, and no import-search path enters the repo.
- Real calls cover every stdlib-capable module group: JSON/base64/NDJSON, TLS,
  filesystem, processes, cloning, UUIDs, iterators, numeric packing, random draws,
  regex, text similarity, clocks, TOML reading, gzip and WAV framing/PCM. Independent
  stdlib outputs or fixed byte/value fixtures supply expected values.
  Ragged packing also checks fixed integer-boundary/numeric-row frames with
  immutable tuple output in base installs and immutable NumPy arrays with the
  `pack` extra. A completeness gate derives every public module from the snapshot
  and requires an explicit base or required-extra behavior probe for each; adding a new module without installed
  consumer evidence fails with the missing probe named. Successful probes also
  verify each assigned module actually loaded from the consumer environment.
  The exact stdlib compression/archive aliases instead require their declared
  provider module identity and an origin in the base interpreter. This narrow
  exception preserves the ordinary installed-product origin checks.
- Missing crypto, msgspec, Pydantic, zoned time, sorted/persistent containers,
  TOML writing, YAML and protobuf capabilities must raise an actionable
  `pip install 'unstd[extra]'` error. Lazy iterator and bridge entry points are
  called, not merely imported.
- The host's mypy checks an external strict consumer against the fresh
  interpreter's installed package. A successful fixture uses `assert_type`; a
  negative control requires thirteen real type errors (twenty on Python 3.15), including invalid explicit
  durability/backend policies and compression return types, so a missing typing marker or
  `Any` leakage cannot masquerade as success. No type checker is installed into
  the consumer environment. Required-extra runs also check installed native
  Zstandard and libarchive typing with independent negative controls for both
  wheel routes.
  Python 3.15 consumers also check the new Base64 padding, wrapping,
  ignored-character and canonical-validation keywords with accepted signatures
  and rejected argument types.

`clean_environment()` retains only platform/launch variables and an explicitly
selected cache directory. Inherited Python import paths, virtualenv selection,
uv index/install/build policy and mypy paths cannot affect a probe. A poisoned
environment test exercises that filter through a real consumer process.

Platform coverage and combinations of multiple extras remain separate evidence.
Per-extra runtime probes follow the versions in the frozen session lockfile;
the dependency-floor job's re-resolved lock therefore drives its floor consumers.
The lane itself needs no registry access.

The [fs integration suite](../../../product/fs/integration/README.md) separately
checks real Linux native/fallback cancellation and error parity, including the
test-only ELF observer. The [production qualification](../../../../../bench/reports/fs/production/README.md)
retains direct/sdist-installed suite receipts, compiler-failure evidence, and
Consumer workload profiles tied to the tested wheel bytes.
