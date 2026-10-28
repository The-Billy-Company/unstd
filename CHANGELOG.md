---
doc_radar:
  sentinels:
  - file: src/unstd/rex/profile.py
    contains: ['_parser.parse(pattern, flags)']
  - file: src/unstd/rex/patterns.py
    contains: ['if started:']
  - file: pyproject.toml
    contains: ['"msgspec>=0.22"', exclude-newer = "P2D", '"blake3>=1.0.10"', '"pyyaml>=6.0.3"', 'select = "cp3{12,13}-macosx_x86_64"', sysconfig.get_config_var("CFLAGS")]
  - file: .github/workflows/ci.yml
    contains: [github.sha || github.ref, 'version: "0.13.5"', uv sync --locked]
  - file: .github/workflows/release.yml
    contains: ['TAG: ${{ github.ref_name }}', Build and test CPython wheels, 'UV_MANAGED_PYTHON: "1"', python -m tests.tooling.release.wheels]
  - file: src/unstd/serde/structs.py
    contains: ['Callable[[Decimal], object]', 'decimal_format: _DecimalFormat']
  - file: src/unstd/compress/engines/reader.py
    contains: [_factory = _accelerated.Inflater, '_zlib_globals["crc32"] = _crc32']
  - file: native/compress/src/adapter.c
    contains: [libdeflate_gzip_decompress_ex(, MAX_OUTPUT_BYTES]
  - file: src/unstd/time/timeutil.py
    contains: [strftime = _time.strftime, gmtime = _time.gmtime, localtime = _time.localtime, strptime = datetime.strptime, combine = datetime.combine]
  - file: src/unstd/yaml/__init__.py
    contains: ['if name in ("pyyaml", "ruamel"):', 'globals()[name] = backend', 'globals().update((key, getattr(codec, key)) for key in __all__)']
  - file: src/unstd/crypto/token.py
    contains: [_DEFAULT_MAX_TOKEN_CHARS = 8192, def prepare_verifier(]
  - file: src/unstd/rand/draw.py
    contains: ['"PCG64", "PCG64DXSM", "SFC64", "Philox"', _py = _random.Random(seed)]
  - file: src/unstd/audio/wavx.py
    contains: [def _read_int16_numpy(, 'return cast("NDArray[_np.int16]", arr.copy())']
  - file: src/unstd/toml/__init__.py
    contains: ['return _dumps(_common_mapping(data), sort_keys=sort_keys)']
  - file: hatch_build.py
    contains: [UNSTD_GZIP_ZLIB_RS is a local qualification experiment;, release wheels require a reviewed source/license/SBOM closure]
  - file: tests/tooling/release/wheels.py
    contains: [def _validate_runtime(, 'if version("cibuildwheel") != "4.3.1":']
  - file: bench/speeds.py
    contains: ['current_environment = environment()', '"provenance": current_provenance']
  - file: tests/tooling/release/verify.py
    contains: [def _macos_floors(, def _macos_payload(, Mach-O deployment version]
  - file: src/unstd/iters/consecutive.py
    contains: ['yield from batched(iterable, n)', 'previous, view = tee(view)', 'window.extend(islice(it, step - 1))']
  - file: src/unstd/iters/distinct.py
    contains: ['candidate = key(element)']
---

# Changelog

<!-- towncrier release notes start -->

## [4.0.3] - 2026-10-27

### Added

- `unstd.cache.Cache` persists JSON results and typed codec artifacts across local
  workers without a new runtime dependency. SQLite WAL storage supports
  namespaces, expiration, optional FIFO bounds, consistent batch reads and atomic
  batch writes. `get_or_compute` coordinates same-key producers while allowing
  independent keys to fill concurrently; mutations and clearing fence in-flight
  producers. Producer failures publish nothing, though crashes and expiry can
  cause recomputation. JSON reads return independent values, and typed codecs
  validate both cold and warm results. Empty batches avoid I/O.
- `unstd.dataset` builds immutable indexed JSON snapshots in one streaming pass.
  Readers can count, index, slice, select rows in request order and process bounded
  batches without retaining the corpus. Snapshots publish atomically and support
  explicit SHA-256 integrity checks. Optional bundled C kernels accelerate
  checked offset lookup and copying; portable installs retain the same API.
- `unstd.math` and the optional `math` extra provide eight NumPy operations:
  stable top-k retrieval, row normalization, cosine scoring, mean-square energy,
  Pearson correlation, weighted pooling, grouped centroids and read-only audio
  frame views. Callers select float32 or float64 calculation precision. Optional
  portable C kernels specialize small calls, dense top-k selection, grouped means
  and frame views, retaining NumPy for larger reductions and exceptional
  arithmetic policies.
- `serde.jsonx.iterload` reads top-level JSON arrays with memory proportional to
  the chunk and largest record. Text, binary, gzip and short-read streams support
  per-record decoder hooks and UTF-8/16/32 binary encoding detection. Exhausting
  the iterator validates the closing bracket and trailing input; callers retain
  ownership of the source. The native default path decodes bounded batches,
  while custom hooks remain lazy per item and fallback never replays callbacks.
- `serde.jsonx.dump_array` writes generators as compact JSON arrays with bounded
  buffering, accelerated per-record encoding and batched text or binary writes.
  Gzip streams, short writes and the stdlib fallback are supported. The function
  returns the record count and leaves the caller's stream open.

### Changed

- Large JSON arrays and objects no longer discard a complete native parse after
  crossing the decoder's 65,536-value proof limit. Validation work is bounded by
  source length, retaining depth and recursion limits, precise-number fallback,
  stdlib hooks and diagnostics.
- Native cloning now handles exact mutable sets, default slotted dataclass state,
  canonical Pydantic model reconstruction and bounded flat temporal sequences
  within the shared graph traversal. Shared identities, caller memo aliases,
  frozen and inherited slots, source isolation, private model aliases, state
  order, iterator errors and custom copy protocols retain their contracts. Model
  setup occurs only when a model is encountered. Bounded temporal policy probes
  preserve live policy changes and walker changes across copy hooks; foreign
  type-membership callbacks stay on the canonical Python path.
- Base-install edit distance removes equal prefixes and suffixes before computing
  the remaining edits, reducing work for long, near-identical strings in bundled
  C/Rust kernels and the Python fallback. Results and subclass/sequence behavior
  remain unchanged. Native scans and residual work stay bounded; RapidFuzz's
  public binding is unchanged. Source installs must rebuild native extensions
  to use the native improvement.
- Portable Jaro–Winkler matching indexes long exact strings in linear time and
  storage while preserving scores and greedy match order. With the `text` extra,
  `best_match`, `extract` and `cdist` use RapidFuzz's native batch kernels for the
  built-in `jaro_winkler` scorer and ordinary string lists or tuples, preserving
  cutoffs and stable ties. Custom scorers and general iterables retain their
  existing behavior; native matrices require NumPy.

### Fixed

- `fs.ensure_dir(..., check_first=False)` retains `Path.mkdir`'s creation attempts
  and audit events. The default keeps existing directories as audit-free no-ops.
- Native JSON array decoding resumes after tracing, profiling or monitoring is
  disabled on Python 3.12–3.14, while active observers retain the Python path.
- Windows native math kernels construct quiet NaNs without triggering spurious
  floating-point exceptions, preserving native results and input validation.
- Release qualification covers the new cache, dataset and math surfaces, keeps
  wheel-test dependencies within the audited lock, and updates cross-platform
  test and benchmark contracts.

## [4.0.2] - 2026-10-25

### Fixed

- Isolate the release bootstrap regression from setup-uv's inherited interpreter
  selection so both its positive and missing-runtime controls run in CI.
- Source builds of wheel-test dependencies use the container's compiler flags
  with the admitted runtime's headers and ABI. A private CPython 3.13+ sysconfig
  overlay prevents Clang-only managed-runtime flags from reaching GCC in musl
  builds; Python 3.12 retains its existing path.
- Process environment qualification compares complete child results with the
  running stdlib, including Windows ARM startup entries. Native random GC-signal
  qualification increases bounded work only when a platform has not delivered
  its timer, while retaining live collector checks and output invariants.

The 4.0.1 tag failed its qualification test and was not published to PyPI.
This release includes the 4.0.1 build fixes and the 4.0.0 feature updates below.
Their tags and qualification evidence remain unchanged.

## [4.0.1] - 2026-10-25

### Fixed

- Linux wheel builds bootstrap the locked uv frontend using the target
  interpreter already provided by cibuildwheel. An older frontend no longer
  tries to provision the development Python pin before it can update itself.
- Wheel-test source builds authenticate `wheel` on every supported platform,
  including CPython 3.14's immutables build, through the locked build closure.
  Build dependencies retain hash verification.

The 4.0.0 tag failed release qualification and was not published to PyPI.
This release includes its changes; the tag and its evidence remain unchanged.

## [4.0.0] - 2026-10-25

This major release changes `clone.deep` to preserve shared references instead
of duplicating them. Callers relying on the previous alias-duplication behavior must review their
usage. `clone.asdict`, `copy` and `deepcopy` retain their existing contracts.
The [October 25 change index](docs/updates-2026-10-25.md) links each module
guide and explains the scope of the retained performance evidence.

### Cloning and compatibility

- `clone.deep` now preserves shared-reference identity throughout each call,
  including aliases crossing builtin containers, dataclasses, models and custom
  `__deepcopy__` implementations. Repeated references to one source object reach
  one cloned object, keeping graph relationships intact while avoiding duplicate
  work and allocations. The native builtin path uses the same identity memo as
  the Python walker. Separate calls still produce independent clones.

  Native builtin graphs now track identities in a pointer table and export them
  to the Python memo only at boundaries that require it. This reduces the cost
  of preserving identity on distinct trees while retaining aliases across custom
  copy hooks and object state.

  Plain dataclasses also use native reconstruction with the enclosing memo,
  reducing Python calls for allocation and state copying. The native layout cache
  uses weak class references and invalidates on class changes; custom allocators
  and attribute access keep the Python recipe.

  This changes the previous deliberate alias-duplication behavior. `clone.asdict`
  continues to match `dataclasses.asdict`, including copying repeated structural
  positions separately; the stdlib `copy` and `deepcopy` re-exports are unchanged.

- `clone.deep` detects builtin cycles in the native traversal, avoiding the
  repeated Python recursion that preceded whole-graph `deepcopy`.
  The native copier fills lists and tuples directly, presizes dictionaries, and
  budgets container visits rather than scalar leaves so large scalar arrays stay
  native. Dataclass/model state and `clone.asdict` fields now reuse native copying
  at their structural boundaries.

  Copy hooks, mutable isolation and cyclic alias preservation retain their
  existing contracts. Shared-reference preservation is described above. `clone.deepcopy` remains the
  stdlib re-export. Paired CPU and elapsed-time profiles for Python 3.13 and 3.14,
  including collector-enabled runs, are recorded in
  `bench/reports/clone/clone-cpu-results.md`; gains depend on the data shape.

- Accelerate mixed builtin/dataclass graphs with a native continuation that uses
  the same memo as Python copy hooks. Per-call class recipes reduce repeated
  dispatch while keeping shared references, live-source iteration, construction
  order and fallback protocols. Memo-visible mutable results remain tracked and
  contain only initialized items, including during reentrant inspection or
  mutation. Native recursion follows Python's frame headroom on CPython 3.12–3.14.

  Preserve ordinary mapping contents and types when Pydantic creates a filtered
  private-state view, and restore completed mutable-container memo entries after
  descendant overrides. CPU measurements use public Python APIs, including wrapper
  and output-destruction costs, against the finished shared-identity implementation.

- Copy scalar sequence prefixes in an allocation-free native loop and fill initial
  adjacent runs from a completed private-memo child. Live source mutation,
  shared identity, positional `asdict` copying and caller memo precedence remain
  covered by ordinary, forced-GC and sanitizer tests. Tighten common clone latency
  guards to 10% using matched wall/CPU batch ratios, retaining raw measurements and
  rejecting malformed pairs so worker drift cannot disguise a real slowdown.
  Keep Linux audit launchers free of inherited ASan preloads while restoring
  instrumentation at every actual Python exec.

- Qualify native cloning on the Linux server with independently built
  Python 3.13/3.14 wheels, shared-identity graph/dataclass route checks and a
  forced-GC/allocation-failure audit. Extend the performance workflow to all
  25 common clone shapes with GC enabled, preserving each baseline's own native
  entrypoints and retaining evidence before a CPU or wall latency guard fails.


### Serialization

- JSONX keeps wide builtin trees and two-space indented documents on its native
  encoding paths, preserves ASCII Unicode escapes without serializing a tree
  twice, and avoids reparsing large objects with early floating-point values.
  Python 3.12 indented floats retain stdlib's exact numeric spellings. Other
  formatting, callbacks, unsupported values and interpreter limits retain their
  existing stdlib fallbacks. Speedups depend on the payload and Python version;
  the focused JSONX throughput benchmark records both gains and unchanged cases.

  Float-record encoding adds a bounded native writer with operation-local float
  and string spelling caches. Strict canonical snapshots use owned native copies
  when the original Python walker, builtins and observers permit acceleration;
  the original identity encoder still runs. Partial snapshots remain private
  across collection, shared branches remain independent, and unsupported trees
  retain their Python validation and error behavior. The retained safety tests
  cover live-source mutation, forced collection, metadata changes, private-output
  discard and cache cleanup. Existing public Python benchmarks qualify these
  changes; they do not establish a universal 100× improvement. Separately
  qualified shared-branch cases exceeding 100× are recorded in the
  [shared-branch evidence](bench/reports/serde/jsonx-100x.md).

- JSON encoding can reuse completed flat branches within a single call for
  eligible shared-row lists, retaining exact output, recursion and logical visit
  limits, fresh output allocation and visibility of mutations between calls.
  Safe ASCII strings use bulk copies. Wide JSON record decoding now proves each
  child before its siblings, avoiding a redundant parse caused by the previous
  64-entry sibling queue. Benchmarks label shared and independent inputs
  separately and retain the existing workloads.

- JSON release wheels now require active native JSON, float, ASCII, identity and
  NDJSON paths during installed-wheel qualification. Performance CI retains paired
  installed JSON record timings on Linux and macOS and rejects wall-time
  regressions above 15%. The JSON throughput driver can require native dispatch
  and enforce an explicit slowdown limit while preserving raw evidence.


### Compression

- Optimize gzip streaming for dataset, upload, HTTP and journal use cases:
  Linux selects zlib-ng's SIMD inflater; macOS retains platform libz and releases
  the GIL safely during large native reads. Per-decoder locking protects state,
  and oversized flush modes cannot silently finish a stream. Interpreter framing,
  bounded reads, member checks, error delivery and caller-owned buffers remain
  covered by compatibility tests.

  Add `zlibx` for accelerated zlib wire operations, with the interpreter's exact
  incremental decoder. Add original-module ports for bz2, lzma, stock zlib,
  zipfile, tarfile, optional python-zstandard and libarchive-c, and Python 3.14's
  compression.zstd. Original modules retain class, exception, context and qualified
  import identity. Stock zlib remains available for compressed-byte hash contracts.

  Retain paired Mac/Linux throughput evidence, an external-codec guarantee
  matrix, frozen consumer fixtures and a four-platform/interpreter regression
  guard. Four independent upload streams measured 1.83× faster on Mac and 4.58×
  on the Linux server; gains depend on workload and hardware.

- Eligible whole-buffer `gzipx.compress` calls use the bundled native encoder for
  exact bytes, public levels 6 or 9, and integer `mtime=0`. Conservative input
  guards retain the existing provider for unsuitable payloads. Level 6 maps to
  native level 1 with an owned three-candidate hash-table matchfinder; level 9 maps
  to native level 6. Compressed bytes can change while the recovered payload
  remains the contract. Stream writers and compressor copies keep their existing
  providers, and native failures are not replayed.

  Linux x86_64 builds can include reviewed ISA-L 2.31.1 source using a local NASM
  or Yasm assembler. Verified vendored sources are copied and patched only in the
  private build directory. Supported CPUs and admitted stream shapes can select
  ISA-L before decoding; other builds and streams retain their existing routes.
  No mandatory runtime dependency or universal speedup is claimed.

- Accelerate representative gzip request encoding and dataset materialization in C
  on both Linux and Mac. Add a private, runtime-dispatched Linux x86_64 streaming
  decoder with verified source and local assembly builds, strict read budgets,
  code-tree validation, input accounting and persistent error semantics. Preserve
  framing, CRC/ISIZE checks, concatenated members, seeking, source ownership and
  staged public readinto behavior. Stateful writers and compressor checkpoints
  retain their existing providers. Qualify both machines, packaged artifacts,
  malformed and partial histories, sanitizer runs and a member exceeding 4 GiB.

- Accelerate highly repetitive gzip streams on Mac ARM with bounded C selection
  of the already imported zlib-ng provider. Preserve Apple libz for ordinary
  dataset streams and zlib-ng's SIMD path on Linux. Selection occurs before any
  body decoding and preserves gzip framing, source read budgets, bounded output,
  CRC/size checks, concatenated members, seeking and staged public `readinto`.


### Numeric data and containers

- `unpack_array` and `unpack_array_portable` accept keyword-only element `count`
  and byte `offset` arguments. Callers can decode headers, body regions, and
  selected records without copying slices of the input. NumPy retains its
  zero-copy views and backend-specific dtype behavior; portable decoding returns
  owned Python lists with explicit range validation.

- Portable array packing and the base-install fallback avoid scalar boxing for
  matching one-dimensional integer and float64 arrays. Exact stdlib arrays need
  no extra; exact NumPy arrays also support logical one-dimensional strides and
  byte-order conversion. Struct validation remains authoritative for other
  inputs, including float16/float32 NaN conversion and custom iterators.

- Add `unstd.pack.ragged.RaggedDecoder` for immutable numeric frames containing an
  integer boundary table and contiguous rows. It validates geometry, monotonic
  boundaries, and optional float finiteness, sharing exact immutable bytes or
  snapshotting other exporters before validation. The optional CPython validator
  uses NEON/SSE2 or portable integer-bit scans without a NumPy C ABI; base installs
  retain a stdlib implementation and immutable tuple results. No input validity
  or decoded-result cache is used.

  Large NEON float32 scans use an exact unsigned maximum of magnitude bits,
  reducing inner-loop work while retaining strict NaN/infinity rejection. Smaller
  frames retain the existing classifier. Complete installed-wheel and current
  consumer-pipeline qualification on Linux and macOS is documented separately;
  the requested 100× complete-decoder target remains unmet.

- Accelerate sorted collection rank and positional queries with bundled CPython kernels
  and compatible subclasses of the upstream types. Retain the original classes
  when native acceleration is unavailable or the backend version is unqualified;
  Map keeps its existing native backend. Load each container family on demand,
  preserving missing-extra errors. Add a paired startup/operation profile with
  Linux and macOS evidence and include containers in native wheel/sanitizer checks.

- Accelerate existing SortedList prefix/suffix slice deletion and increasing
  integer list/tuple updates on Linux and macOS. Stage replacement blocks before
  commit, reject storage aliases and callback-bearing layouts, revalidate after
  allocation, and retain upstream behavior for unsupported cases. Add paired
  mutation benchmarks, deterministic allocation-failure and reentry tests, and
  test-only sanitizer hooks. Keep scalar mutation algorithms upstream. Tighten
  read-query fallback ownership so comparator, metadata and keyword callbacks
  retain upstream finalizer timing.

- Accelerate ordinary `rand.Generator.shuffle` lists with an optional CPython
  kernel that preserves stdlib permutations and engine states. Hoist invariant
  work in fallback bulk draws and use NumPy's direct unit-float generator.

  Extend `rand.ints` and `Generator.ints` with explicit `seeds=` batches using
  stateless SplitMix64-v1. This opt-in changes the seeded stream, preserves normal
  RNG states, and has matching native/Python implementations. Captured-consumer
  benchmarks retain separate count-phase, full-workflow and compatibility results
  on macOS and Linux.


### Processes, files, audio and iteration

- `proc` now uses an optional bundled C backend for isolated macOS process creation
  and Linux/macOS captured text output without stdin. Linux retains its optimized
  stdlib launch path. Current Git, metadata and
  log shapes have paired native/Python/stdlib profiles; stdin and unsupported
  platform shapes retain stdlib behavior. Captured output, timeout bytes, audit
  events, descriptor isolation and kill/reap contracts remain covered by native,
  fallback, debug-allocator and sanitizer qualification.

- `proc` uses Linux pidfds and macOS kqueue exit notifications to avoid polling
  sleeps after captured pipes close. Bounded waits fall back to stdlib when the
  kernel facility is unavailable or restricted. Process creation, captured I/O,
  timeouts, cancellation cleanup and the public API retain their existing
  contracts; no optional dependency is added. Paired performance evidence lives
  in `bench/reports/proc/`.

- Filesystem traversal reduces path construction and directory ownership overhead
  while retaining descriptor-relative symlink protection and cleanup on early
  exit or failure. Existing-directory checks avoid a failed mkdir, and strict
  atomic writes skip redundant parent creation after verifying the directory.
  File syncing, permissions, and directory-sync acknowledgments are preserved.

- Linux CPython builds accelerate ordinary byte and default UTF-8 atomic writes
  through a bundled native backend. Missing parents, other codecs, subclasses,
  custom bindings, unavailable native capabilities, and optional compiler failure
  retain the Python implementation. Native errors never retry the write.
  The backend needs no extra or third-party runtime dependency; other platforms,
  interpreters and free-threaded builds keep the complete Python path.
  Release verification accepts the typing stub on every platform while restricting
  the compiled filesystem binary to Linux wheels.

  Both paths retain atomic replacement, private scratch creation, mode preservation,
  and file/directory syncing. Native cancellation releases acquired resources
  before commit and reports committed state afterward. The Python writer now also
  identifies a successful rename interrupted before its call returns.
  Cleanup failures retain the primary exception and add notes. Linux close is
  never retried, and unavailable native entropy declines before scratch creation.

  Installed direct wheels and sdist rebuilds exercise native and forced-Python
  routes against 13 consumer workload shapes. Linux CPython 3.12–3.14, compiler
  failure fallback and sanitizer checks retain their evidence in
  [`bench/reports/fs/production/`](../bench/reports/fs/production/README.md).

- Audio adds `wavx.decode_pcm` for interleaved little-endian PCM voice frames,
  with owned writable NumPy output or dependency-free PCM16 lists. Floating
  NumPy decoding converts directly and scales in place to avoid temporary arrays.
  `wrap_pcm` accepts contiguous buffers, including bytearrays and typed memoryviews,
  so accumulated narration can be wrapped without an intermediate bytes copy.

- We prepare wide sliding windows in linear time, move strided window advances
  into `deque.extend`, and reduce Python overhead in non-strict chunks and keyed
  deduplication. The iterator API, lazy reads, mixed-key equality, and bounded
  retention stay the same; no extra or native backend is required.

- Large one-shot BLAKE3 hashes, MACs, and key derivation now honor process CPU
  affinity on Python 3.12 where the operating system exposes it. CPU-constrained
  Linux workers avoid creating hashing pools sized for the whole host. Python
  3.13+ retains its process CPU count and CPU-count override; platforms without
  affinity support retain the host-count fallback. Digest values and public APIs
  are unchanged.


### Build and verification

- Organize Python source by technical concern. Remove shallow private helper
  packages, use descriptive source names, and group compression engine integration
  under `compress/engines`. Preserve public imports, native extension names, and
  historical regex pickle identities. Update package guides, source provenance
  collection, and saved-revision benchmark loaders for the resulting layout.

- Development now uses Python 3.14.9. Default CI, platform contracts, dependency
  floors, base and partial-extra installs, sanitizer checks, and scheduled chaos
  and concurrency campaigns run on Python 3.14 to match the consuming application.
  Deterministic fuzzing also covers 3.14 while retaining Python 3.12 for Atheris.
  The existing Python 3.12–3.14 compatibility matrices remain required.

- Performance regression comparisons now run independently on Linux and macOS
  with Python 3.13 and 3.14, including for native-source changes. Python 3.14
  exercises its distinct native JSON and clone fast paths. Each platform and
  interpreter measures both revisions on the same worker and retains its own
  evidence; a failure in one comparison does not cancel the others. These
  comparisons detect regressions without promising identical elapsed time across
  different hardware and operating systems.

- Fix clean-runner compatibility across supported Python versions and platforms:
  align the audited uv frontend with the pinned Python download, use a compatible
  RapidFuzz floor on Python 3.14, refresh cached wheel runtimes before security
  admission, and retain platform/runtime dependency markers in artifact verification.
  Document the new optional extras, guard Unix-only benchmark metadata, and qualify
  zipfile's version-specific internal child module against its actual stdlib API.
  Retain a recursive YAML merge finding as a fuzz regression while preserving safe
  parser and product parity checks.
  Avoid whole-block gzip engine selection for small buffered reads, compare shared
  performance workloads before timing added APIs, and bound sanitizer-only stack
  boundary differences without dropping discarded-source recursion checks.
  Reuse the bounded weak type/version cache for native record layouts during
  general graph copying, retaining Python admission and mutation invalidation.

## [3.1.6] - 2026-10-25

### Fixed

- We use admitted managed interpreters for release hosts, Linux and macOS wheels,
  and CPython 3.12 Windows wheels. The pinned builder still owns compilation, repair
  and tests; runtime ABI and TLS checks run before compilation. Linux keeps its
  original compiler settings. Intel 3.12 and 3.13 keep the macOS 10.13 floor and
  optimization flags; 3.14 keeps its 10.15 floor. Tooling and supply checks install
  their declared build dependencies. Packaging 24.2 is the minimum required by
  the supported Setuptools floor.

- We verify the architecture and deployment floor inside each macOS wheel's Mach-O
  payloads, including embedded binaries. A newer minimum OS, malformed load-command
  table or missing deployment command fails release admission even when the archive's
  hashes and metadata are valid. The standalone verifier keeps its standard-library-only
  trust boundary.

- We initialize both speed-comparison phases before timing and retain loaded-module
  hashes and compiler settings with the measurements. A focused gzip diagnostic
  preserves the original read fixtures and native-inflater samples; it has a separate
  check name and cannot replace the full performance gate. Existing speed ceilings
  remain unchanged.

- We check native token-scan interruption at the C call boundary. A signal delivered
  after the verifier returns cannot satisfy the native interruption check.

The 3.1.5 tag failed release qualification and was not published to PyPI. This
release includes its additions; the tag and its qualification evidence remain
unchanged.

## [3.1.5] - 2026-10-25

### Changed

- We expose nanosecond clocks, process and thread CPU clocks, sleep, native datetime
  reads and strict ISO decoders through direct stdlib bindings. They retain native
  precision, exceptions and platform availability without an extra call frame.

- We expose native time tuples, explicit format parsing and date/time assembly through
  direct stdlib bindings. Formatting keeps the platform directives and local-time
  default; parsing and assembly keep their native timezone, fold and error behavior
  without an extra call frame.

- We add lazy `yaml.pyyaml` and `yaml.ruamel` bindings to the original backend modules.
  Loader, dumper, parser, event and node objects keep their exact identity and behavior;
  importing one contract does not load the other backend. Native YAML 1.2 helpers bind
  their original callables on first access.

  We verify both YAML backends in isolated wheel and source-distribution consumers,
  including their exact dependency closure and module origins.

- We fix `toml.dumps`/`toml.dump` writing text stdlib `tomllib` cannot read. ESC in
  plain strings and keys is now spelled `\u001b` instead of the TOML 1.1-only `\e`. UTC
  offsets that are not whole minutes, and `time` values carrying an offset, now raise
  `ValueError` before anything is written, where tomlkit used to emit syntax no TOML
  version accepts. Items already inside a parsed or tomlkit-built document keep their
  spelling. `toml.dumps` is no longer the same object as `tomlkit.dumps`.

- **Bounded token verification and patched crypto tooling.**

  `token.verify` now rejects encoded envelopes longer than 8192 characters before
  scanning, slicing, decoding or hashing. Pass a larger positive
  `max_token_chars=` limit, or explicitly `None` after enforcing a request bound,
  for bulk envelopes. Token bytes, MACs, canonical encoding rules and key errors
  within the bound are unchanged. Verification authenticates before decoding
  claims and checks canonical Base64 lengths and terminal bits directly.

  `token.prepare_verifier(max_token_chars=...)` explicitly prepares a reusable
  size policy and optional native envelope parser at startup. It retains no
  signing keys or request data. Token-only imports keep the direct Python path;
  imports after `digest` reuse an already loaded helper. This avoids imposing
  native loading costs on token-only users or deferring setup to a first request.

  The crypto extra requires `blake3>=1.0.10` for reviewed upstream correctness and
  dependency fixes. Worker caps respect process CPU availability on Python 3.13+
  without per-call probes. Existing algorithms, native stream batching and exact
  `secrets` aliases remain in place.

  Development uses CPython 3.13.16 with patched OpenSSL 3.5.9. CI, release tooling
  and wheel tests run a branch-aware TLS-runtime preflight; no version checks are
  added to crypto hot calls. Package support still starts at Python 3.12. TLS
  contexts remain independently configured; callers may reuse a configured context
  without sharing mutable policy across unrelated callers.

- We read PCM16 as `int16` through the NumPy fallback by copying sample counts
  directly, avoiding float normalization and requantization. Whole-file and
  streaming reads retain owned writable arrays, little-endian dtype, channel
  shape and frame validation. SoundFile routing and float conversions are unchanged.

  We add authenticated patched-libsndfile build tooling, host-qualified SoundFile
  wheels and offline admission of the actual loaded library against separately
  trusted provenance. A required audio-security CI lane builds, admits and tests
  the patched reference artifact. These checks run outside production audio calls.

- **Explicit simulation engines and cheaper generator construction.**

  `rand.Generator(..., backend="numpy", bit_generator=...)` accepts `"PCG64"`,
  `"PCG64DXSM"`, `"SFC64"`, or `"Philox"`. The selected algorithm survives
  reseeding. Existing defaults and seeded scalar/bulk sequences remain unchanged;
  other bit generators are explicit new simulation streams, never secure randomness.

  Ordinary `Generator` construction initializes the stdlib engine once instead of
  seeding from OS entropy before immediately applying the requested seed. Subclasses
  retain their initialized state and virtual `seed()` hook. Draw dispatch and instance
  slots are unchanged. UUID documentation now distinguishes the fallback's 12-bit
  counter from CPython 3.14's 42-bit counter.

- We add an isolated libdeflate 1.26 qualification builder with a fixed reviewed
  manifest, copied source/license hash checks and C11 compilation. Default
  libdeflate 1.25 and compiler settings remain unchanged: 1.26 adds first-use
  Linux ARM64 CPU-identification syscalls and does not establish universal savings.

  We add a local build-only zlib-rs inflater qualification option using a reviewed,
  privately prefixed static archive and mandatory SHA256. Default platform/vendored
  zlib, gzip framing, checked Python allocation callbacks and compiler-free
  fallbacks remain intact. The experiment is blocked from release wheel builds
  pending full source/license/SBOM and platform qualification.

- We reorganize package internals, native sources and benchmark tooling while
  preserving existing public imports, exports and call signatures. Native source
  now lives under `native/<package>/src/`; private extension module names remain
  unchanged. Benchmark commands retain compatibility entry points, with recorded
  evidence and frozen baselines separated from executable tools.

  The root README links to a complete public API inventory and architecture guide.

- We preserve the frozen public API while checking interpreter-derived defaults and
  inherited constructors against their actual native owners. Backend and Python upgrades
  keep their own parameter contracts; owned parameters, fields, wrappers and allocation
  hooks remain strictly checked.

- We keep matrix tests on the managed Python runtime selected by setup-uv, so the
  runner's older OpenSSL cannot replace it. The test dependency floor now includes the
  `pytest.importorskip(exc_type=...)` API used by the suite; the TLS admission policy
  stays unchanged.

  We select the isolated audio environment after the runner starts, so GitHub can
  validate the reusable security workflow before running it. The audio job also selects
  managed Python explicitly.

- We compare every time operation available in the previous release and report new
  native bindings separately. The current revision still requires the full API; existing
  workload definitions, measurements and regression ceilings stay unchanged.

The calendar [native-callable proof](bench/audits/calendar-native-bindings.json)
records the same native functions and bound methods without a Python wrapper.
The separate [absolute timing certificate](bench/audits/calendar-certificate.json)
retains four new ceiling misses from the repeated measurements; those limits
were not raised. The [YAML import diagnostic](bench/audits/yaml-backend-imports.json)
records the remaining one-time namespace cost.

## [3.1.4] - 2026-10-22

### Changed

- Use compiled native bounded inflation and libdeflate CRC32 for gzip streams
  with `unstd[compress]`, while preserving the running interpreter's framing,
  buffering, seeking, ownership and malformed-stream delivery order. Eligible
  complete single-member exact byte buffers use libdeflate after frame and
  checksum validation. Other one-shot cases retain stdlib parsing and
  diagnostics. Writes remain on zlib-ng; source builds without the read
  extension retain the portable zlib-ng stream backend, and base installs keep
  exact stdlib bindings.
- Paired [gzip read measurements](bench/reports/compress/gzip-native-reads-results.json) record
  identical recovered bytes and observed native execution before timing.
  Require the read extension in native wheel and sanitizer coverage, including
  exact input-boundary reads and malformed concatenated-member histories.

## [3.1.3] - 2026-10-22

### Changed

- Speed up gzip reads with the compression extra installed: file streams use the
  running interpreter's stdlib reader, and complete single-member byte buffers use
  zlib's C gzip parser. Native compression remains available. Preserve bounded
  reads, seeking, concatenated members, header quirks, buffer slicing semantics
  and exact stdlib decode diagnostics. Remove the unused native inflater adapter
  and add paired, output-checked read workloads with source and dispatch evidence.
- Close owned files immediately if reader setup fails on older CPython patch
  releases; explicitly supplied file objects remain caller-owned.

## [3.1.2] - 2026-10-22

### Added

- `jsonx.canonical_strict` encodes a defined JSON identity domain using a frozen
  version-1 byte format. It rejects implicit object/string conversions,
  non-string keys, subclasses, non-finite floats, surrogate code points and
  cycles. The legacy `canonical` function and its persisted bytes remain
  unchanged.
- Filesystem writers and moves accept `durable="strict"`; `sync_dir` accepts
  `strict=True`. Strict mode reports directory-sync failures, preflights
  support, syncs new parent entries, and identifies failures after replacement
  as already committed. Existing best-effort defaults remain unchanged. Strict
  moves sync both directories; source-file content flushing remains the caller's
  job.
- `rex.compile_faithful` requires Python regex meaning, including non-multiline
  `$` and nested/POSIX bracket syntax, while retaining native acceleration where
  the parsed pattern and subject support it. Historical `rex.compile` behavior
  remains unchanged.
- `rex.compile_linear` requires Python-faithful native single-match operations
  and raises `rex.LinearRegexError` whenever compilation, a subject, an argument
  shape or a native operation would need backtracking fallback. `requires_ascii`
  exposes subject restrictions. `search`, `match` and `fullmatch` include
  capture projection within a linear subject-length bound for a fixed compiled
  pattern. Iteration, splitting, substitution and scanner operations are always
  refused before matching or callbacks because native suffix rescans can be
  quadratic. Compilation and process limits require a separate application
  policy.
- Added `rand.Generator(seed, backend="stdlib")` for independent, explicitly
  selected random streams. Its default remains stdlib with NumPy installed;
  `backend="numpy"` requires the `rand` extra and selects PCG64 for bulk draws.
  Existing module-level defaults and valid seeded draw sequences remain
  unchanged. Reproducibility requires the same backend and call sequence.
- Added explicit backend-independent fuzzy scorers: `fuzz.indel_ratio` computes
  normalized Indel similarity with or without RapidFuzz, and
  `fuzz.difflib_ratio` always uses stdlib SequenceMatcher. `best_match`,
  `extract`, and `cdist` accept `scorer=` to keep rankings and inclusive cutoff
  decisions stable as optional dependencies change. Existing `ratio` and
  omitted-scorer behavior are retained.
- Added `binpack.pack_array_portable` and `unpack_array_portable`, which always
  use the portable dtype subset and stdlib struct conversion/error rules,
  returning bytes and an owned list of Python scalars. Existing NumPy coercion,
  broader dtypes, and zero-copy results remain available through the original
  helpers. Clarified the historical backend differences in module documentation.
- Explicit-scorer matching consumes mapping keys in iteration order and applies
  identical inclusive-cutoff behavior across backends, including NaN and
  infinite cutoffs. Historical omitted-scorer behavior remains unchanged.
- Added an opt-in Rust prototype for the bounded text kernels and a paired,
  output-checked C-versus-Rust comparison harness. Default and release builds
  keep the existing C implementation: the measured Rust prototype has short-Jaro
  latency and extension-size overhead and does not meet the no-overhead gate.
  Rust builds are selected explicitly with `UNSTD_TEXT_RUST=1` and retain the
  public API, Unicode behavior, resource bounds and portable fallbacks.

### Changed

- Clarify that utility extensions have their own contracts rather than promising
  an import-only stdlib migration. Correct Decimal hook documentation: native
  Decimal encoding bypasses `float_enc_hook`; `decimal_format` controls its wire
  type. Tests pin both string and numeric Decimal output with a hook installed.
- Random bulk result annotations now support runtime type introspection with and
  without NumPy, including early Python 3.12 patch releases.
- Public text and array-helper annotations now support `typing.get_type_hints`
  without optional backends; portable dtype signatures use plain strings.
- Strengthen the required Ruff and mypy gates for production and benchmark code.
  Require complete annotations, validate native stubs and override declarations,
  reject blanket suppressions, and check additional control-flow, awaitable,
  pattern-match, bytes, and optional-value contracts. Replace global
  missing-import suppression with development-only protobuf and sorted-container
  stubs and narrow exceptions for CPython's private regex modules. Preserve
  subclass types from audio writer context managers and retain optional-backend
  runtime behavior. Check Python 3.12–3.14 types for Linux, macOS, and Windows
  in CI, and add real-analyzer positive and negative regression controls.
- Add required full-source SAST and duplicate-code checks to CI and releases:
  Bandit, CodeQL security-extended for Python/C/C++/Rust/Actions, Rust Clippy,
  and Pylint duplicate-code. Findings and incomplete scanner evidence fail the
  gate; reports and diagnostics are retained. Consolidate strict Base64 decoding
  and regex absent-group projection into shared implementations. Document scope,
  local commands, reviewed false positives and finding remediation in
  SECURITY.md.
- Supply chain checks now validate dependency sources and hashes, audit every
  locked extra and build/release tool, and retain a complete CycloneDX
  inventory. The required CI gate also rescans daily for newly published
  advisories.

  Release wheels build from the published source archive with authenticated
  build and test requirements. Correct constraint-file paths for isolated wheel
  builds from that archive. Before PyPI publication, archive, metadata and
  wheel RECORD checks produce a SHA256 manifest that is verified again in the
  isolated Trusted Publishing job. Test reports and dependency evidence stay
  outside the distribution handoff.
- Split long correctness and sanitizer jobs into complete, disjoint shards and
  run type-check targets concurrently. Release builds run alongside validation
  and build Python versions independently. Publication still requires verified
  archives and successful CI for the exact commit; recent repository-owned CI
  evidence can be reused instead of repeating the entire matrix.
- Resource tests now cover streaming memory growth, file-descriptor and scratch
  cleanup, cache lifetime and deep JSON in bounded Linux/macOS workers.
- Native profiles distinguish completed C work from rejected dispatch attempts,
  compare both installed and base public signatures, and reject native-enabled
  baselines. Expanded Unicode, work-budget and recursion regressions, sanitizer
  checks and Python 3.12–3.14 runs retain existing API and fallback contracts.
- Added a shared registry with correctness-checked latency workloads for every
  public operation across all 18 packages, a committed per-function speed
  snapshot, and immutable regression ceilings with recorded runtime/backend
  metadata. Coverage gates reject newly exported APIs without workloads or
  frozen entries. An opt-in performance suite and a revision-comparison workflow
  enforce speeds on compatible workers while keeping ordinary correctness tests
  free of timing assertions. Revision comparisons audit every shared workload
  and report newly added APIs without inventing historical latency ceilings.
- Benchmark calibration now confirms the iteration count with a second batch so
  one descheduled cheap call cannot make all later batches too small.
  Verification reports retain raw wall and thread-CPU samples for every
  initial/recheck attempt. Added alternating independent random-workload
  controls with primitive/source identity evidence. Environment sensitivity
  remains diagnostic: original latency ceilings and failure verdicts stay
  intact.
- Expanded product property, state-machine and boundary tests across optional
  backends, and reorganized shared test support and tooling by responsibility.
  CI now includes macOS/Windows public contracts and each declared extra
  installed alone, plus backend-selecting combinations; missing-extra checks
  require exactly the installed extras.

### Performance

- `clone.deep` now uses a bundled, bounded C walker for eligible exact builtin
  trees. Base-install `fuzz.distance` and `fuzz.jaro_winkler` can use bundled
  exact-string Levenshtein and Jaro kernels. Public signatures, runtime
  dependencies, RapidFuzz selection, Unicode behavior, custom copy protocols and
  documented clone alias/cycle behavior are retained. Unsupported inputs and
  builds without a compiler use the existing Python implementations.

  The paired profile driver in `bench/native_profile.py` checks compatibility
  and actual native dispatch before timing. Native-required Python 3.12–3.14 CI,
  debug-allocation tests, sanitizer CI, and installed-wheel/fallback tests
  exercise the new boundaries.
- One-shot BLAKE3 hashes, MACs, and key derivation now use four fresh workers
  for exact `bytes` inputs from 1 MiB and eight from 4 MiB, capped by CPU count.
  Fresh pools remain usable after a fork and fall back to serial hashing if the
  backend refuses workers. An optional C helper batches small immutable chunks
  from materialized stream lists and tuples into bounded 64 KiB updates for the
  existing BLAKE3 backend. Generators remain eager, mutable buffers retain their
  backend behavior, and compiler-free installs keep the Python path. Public
  APIs, digest values, validation, accepted buffers and legacy BLAKE2b
  identities are preserved. Full and truncated hex output use faster byte-to-hex
  conversion. Paired benchmarks load saved private helpers independently and
  record crossover, medium-input, reusable-stream hex and small-chunk gains
  alongside overhead and scheduling noise.

### Fixed

- Fix `unstd.crypto.digest.derive_key` silently returning the plain BLAKE3
  digest of the material when the context was `None`: the backend treats a
  `None` derive-key context as ordinary hashing, so the derived "key" equalled
  the public content digest. Non-`str` contexts now raise `TypeError` on both
  the serial and parallel paths, matching the backend's existing message for
  other wrong types.
- Fixed native BLAKE3 stream batching delaying pending signals across backend
  updates. Signals are now checked after each successful backend update, so
  cancellation does not wait for thousands of large chunks. Empty-chunk
  traversal keeps its periodic signal checks.
- Create atomic-write scratch files with the existing destination's permissions
  from the outset, preventing retained read descriptors from exposing
  replacement secrets. Copy permissions through the acquired descriptor where
  supported so a replaced scratch path cannot redirect chmod onto an unrelated
  file.
- New `fs.atomic_write` and `fs.atomic_writer` destinations request owner-only
  `0o600` instead of `0o666` before the kernel applies the caller's umask. New
  atomic files and their scratch files no longer grant group or world access by
  default, even under permissive umasks. A normal `0o022` umask now produces
  `0o600`, deliberately stricter than ordinary `open()` creation. Set new-file
  sharing explicitly when needed. Rewrites preserve the existing destination
  mode exactly, including previously configured group sharing. The library never
  changes the process-wide umask.
- `fs.atomic_writer` now rejects modes other than `"w"` and `"wb"` before
  creating directories or scratch files. Previously, unsupported read and
  exclusive-create modes could silently replace an existing destination with an
  empty file.
- Protect default filesystem enumeration against directory and ancestor symlink
  replacement on platforms supporting descriptor-relative no-follow traversal.
  Bound live handles by traversal depth and close them on termination. Preserve
  the portable fallback with directory-identity checks and document its weaker
  concurrency guarantees and the limits of returned path names.
- Reject explicitly empty TLS CA, certificate and key paths. An empty CA pin can
  no longer restore default public trust, and empty identity paths cannot
  silently omit mTLS. Use None to omit a path.
- Use Python semantics for nested character sets and set operators that the
  native regex engine interprets differently, preventing character denylist
  bypasses. Escape-aware classification preserves literal escapes and documented
  native POSIX bracket expressions.
- Reject duplicate explicit YAML keys even when their mapping uses a merge. Safe
  loading now checks source keys before merge flattening; legitimate single
  overrides, merge precedence and reused anchors remain supported.
- Fix exponential merge-key amplification in `yaml.loads`, `load` and
  `load_all`. Merging the same anchor repeatedly at each nesting level copied
  every inherited key/value pair again, so a 2 KB document never finished
  loading. Redundant merged pairs are now bounded while preserving merge
  precedence, key order and duplicate-key rejection.
- Fix two cases where `yaml.dumps`, `dump` and `dump_all` wrote a different
  value than they were given. Strings containing NEL (U+0085) are now
  double-quoted with an escape instead of single-quoted, where readers folded
  NEL into a space. Floats from `parse` keep their original spelling only while
  it still denotes the same number; exponent spellings that the backend would
  truncate (for example `3.403832445554453e+16`) are written in the shortest
  exact form instead.
- Fix `serde.structs.Codec.decode_or` so a payload whose JSON string content is
  not valid UTF-8 (or a `str` payload carrying lone surrogates) returns the
  default as a malformed cache miss, instead of leaking the `UnicodeError`
  msgspec raises for it. `Codec.decode` is unchanged and still raises exactly
  what msgspec raises; hook exceptions other than msgspec validation errors
  still propagate from `decode_or`.
- Finish partial NDJSON writes before counting a record or consuming the next
  one. A zero write count, or None from a standard io.IOBase stream, now raises
  BlockingIOError instead of silently reporting success with missing data.
  Generic write-only sinks may still return None to acknowledge complete writes.
  Existing sink exceptions and caller ownership remain intact.
- Fix whole-file and streaming PCM16/PCM32 writes to preserve raw integer sample
  counts with and without soundfile. Clip before narrowing and preserve integer
  semantics for mixed signed and large Python integers. Independent PCM-byte
  tests cover the supported backend and source-width combinations.
- The pure-stdlib WAV writer now validates every multichannel sample with the
  same numeric check used for mono input. Numeric strings are rejected instead
  of being silently converted to integer PCM values. Rejected chunks emit no
  bytes, and an incremental writer remains usable afterward.
- Fix WAV fallback writers accepting frames that contradict the channel layout.
  The pure-stdlib floor now rejects ragged multichannel lists with `ValueError`,
  and both stdlib fallbacks reject `Writer.write` chunks whose channel count
  differs from the writer's `channels`, matching NumPy and soundfile instead of
  writing a data chunk the header misdescribes. A closed fallback `Writer` now
  raises its documented `ValueError` on further writes instead of an internal
  `AttributeError`.
- Reject nonpositive WAV block sizes before reading input, preventing zero-size
  nonprogress and negative-size whole-file reads. Fallback whole-file decoders
  also reject declared trailing partial PCM frames instead of silently dropping
  their bytes.
- Fix the stdlib `unpack_array` fallback to count memoryview bytes instead of
  its first dimension. Contiguous typed, multidimensional, and scalar views now
  decode consistently with NumPy. Document that NumPy view writability follows
  the buffer.
- Fix two defects in the `unstd.pack.binpack` struct array path (the portable
  helpers and the no-NumPy fallback). Dtype sizes were parsed with `int()`, so
  spellings such as `"i4 "`, `"i0_4"`, `"i+4"` or non-ASCII digits were accepted
  as `int32`; only exact typestrs and documented aliases are accepted now.
  Non-memoryview buffers were also sized with `len()`, so an 8-byte `array("i")`
  was rejected as "buffer size 2" and contiguous ndarrays failed or were
  misreported. Every contiguous buffer exporter is now measured in bytes.
- The stdlib array-packing fallback now rejects empty and incomplete dtype
  strings with `struct.error`, matching its other unsupported dtype errors,
  instead of leaking an internal `IndexError`.
- Fix `clone.deep` and `clone.asdict` isolation for datetime/time values
  containing custom mutable timezones. Delegate their copy protocol to
  `deepcopy`, while retaining shared immutable values for naive and exact stdlib
  timezone types.
- Clone's per-class metadata caches use weak keys so dynamically generated
  classes can be collected after their instances are released.
- Native clone stack-guard exhaustion now defers to the original Python walker,
  preserving shared-branch duplication in acyclic trees under C recursion
  pressure.
- The native clone extension now builds with glibc headers, whose process-level
  `clone` declaration conflicted with the extension's internal C function name.
  The Python `clone` method and cloning behavior are unchanged. Strict native
  builds no longer fail, and ordinary Linux source builds retain clone
  acceleration instead of silently falling back to Python.
- Fix `timeutil.span` to measure elapsed instants across DST transitions within
  a shared IANA zone. Repeated local times and half-hour transitions are covered
  by regressions, including reverse durations and string/datetime input
  agreement. Offset arithmetic also preserves valid intervals near the datetime
  year limits without constructing UTC endpoints that would overflow. Custom
  timezones returning no UTC offset follow the floating-input policy: strict
  mode rejects them; `assume_utc=True` treats their wall fields as UTC.
- Fix `timeutil.from_epoch_ms` returning instants off by up to tens of
  microseconds for timestamps beyond roughly 2^33 seconds from the epoch (for
  example `9999-12-31T23:59:59.998993Z` for `253402300799999`). It divided by
  1000 in floating point; it now splits whole seconds from milliseconds exactly,
  keeps the same out-of-range `ValueError`, and still converts the instant into
  *tz*.
- Fix iterator contracts exposed by adversarial tests. `partition` freezes each
  predicate result to a boolean once, preventing mutable or side-effecting truth
  values from dropping or duplicating items across its two outputs.
  `unique_everseen` now deduplicates equal hashable and unhashable keys across
  one shared history (including sets/frozensets), preserves the first object,
  and propagates TypeError raised by hashable-key comparisons instead of hiding
  it.
- Fix `iters.windowed` with the default step reading `n - 1` items from its
  input as soon as it was called. An abandoned window iterator no longer drains
  a shared source, and a failing source now raises on the first pull instead of
  from the `windowed(...)` call itself; strided and zero-width windows were
  already lazy.
- Failed NumPy-backed `seed()` calls now preserve both scalar and bulk state.
  Bulk helpers consistently reject negative/noninteger counts and reversed or
  empty intervals before drawing, including when requesting zero integers. Bulk
  floats also reject nonfinite bounds or spans without consuming state,
  including for zero draws; equal finite bounds remain valid.
- Fix NumPy-backed `rand.choices` and `Generator.choices` accepting weights with
  a zero or negative total: normalizing by that total flipped all-negative
  weights into valid probabilities and drew by magnitude. Both engines now raise
  `ValueError` for a non-positive total before consuming any draws, and an
  all-zero vector no longer emits a NumPy divide warning.
- The portable Jaro-Winkler scorer now validates `prefix_weight` within `0..1`
  before scoring, matching RapidFuzz even for empty or identical strings. NaN
  weights preserve RapidFuzz's default cutoff behavior instead of leaking NaN
  scores when the prefix bonus applies.
- Fuzz replay and Atheris supervisors now observe POSIX worker exit without
  reaping the leader until its owned process group has been cleaned up. This
  closes the released-PID reuse window before group signaling; cleanup errors
  still fail. macOS Python 3.12 uses a contained stdlib `ctypes` adapter for the
  OS `waitid` operation that its Python wrapper does not expose.
- Verification workers now distinguish macOS's zombie-only process-group `EPERM`
  race from real cleanup permission failures using bounded OS evidence. Live,
  foreign, or uncertain process membership still fails cleanup, and direct
  children are reaped even when group cleanup fails. Resource probes, Python
  workers, fuzz workers and chaos campaigns share this cleanup rule.
- Isolated per-extra consumers preserve the frozen manifest's complete resolver
  quarantine policy, including package exceptions, fixing locked exports after
  policy changes without rewriting the lockfile.

## [3.1.0] - 2026-10-20

### Performance

- JSON encoding can use an optional CPython native guard that checks exact
  builtin trees and calls orjson while retaining the GIL. Larger text records
  avoid 3.0.0's Python snapshot budget and stdlib fallback. Compiler-less source
  builds retain the portable path; release wheels include the guard.
- Native default spacing preserves punctuation and escaped quotes inside text,
  avoiding the stdlib fallback for larger string-heavy records.
- Untyped JSON decoding reuses msgspec's arbitrary-precision integer and correctly
  rounded float decoder, with a bounded native proof enabling orjson for larger
  records. CPython 3.14 native dispatch preserves Python function metadata and
  falls back during instrumentation. Custom options, unsupported inputs and
  native errors retain stdlib behavior.
- NDJSON writes use the same guarded encoder's native trailing-newline option,
  avoiding redundant public dispatch and an extra output copy per record.
- Whole-blob NDJSON reads batch native parsing over strict physical newline
  boundaries. Streaming reads, callbacks and error diagnostics keep their
  existing behavior.
- Added an output-checked consumer fixture comparison with source and fixture
  fingerprints, alternating before/after CPU batches, direct-call companions and
  an optional release-regression gate. The measurement report retains residual
  costs relative to the permissive 2.1.1 codec; no universal speedup or
  zero-overhead compatibility claim is made. Public APIs are unchanged.

### Fixed

- Native codec reinitialization keeps earlier helpers' configuration checks and
  Python fallbacks valid. Module destruction also releases watchers and owned
  references when garbage collection has not run the clear hook first.

## [3.0.0] - 2026-10-19

### Breaking

- `serde.jsonx` now guarantees the running stdlib's JSON defaults and semantics.
  `dump` and `dumps` use `ensure_ascii=True` and default spaces, replacing the
  previous compact, unescaped output. To retain compact text, pass
  `ensure_ascii=False, separators=(",", ":")` explicitly. Dataclasses, UUIDs and
  plain enums now require an explicit encoder or `default`, as they do in
  stdlib `json`; orjson-only type acceptance is removed.
- JSON numbers retain their values: arbitrary precision integers no longer fail
  on encode or silently become floats on decode, and non-finite floats follow
  stdlib's `NaN`/infinity behavior instead of silently becoming `null`.
  `allow_nan=False` rejects them. Unicode and lone surrogates, UTF-16/32 binary
  input, supported subclasses, key coercion/sorting, callbacks, custom codec
  keywords, error diagnostics and interpreter limits follow stdlib too.
  `dump` preserves streamed writes, including partial output before a later
  encoding or write failure; `load` reads once through the guarded parser.
- `jsonx.dumpb` now preserves exact compact stdlib float notation across
  backends, which can change serialized bytes. NDJSON and Base64 JSON helpers
  preserve the same numeric values. NDJSON wraps Unicode, integer-limit and
  recursion failures with physical line numbers and honors `skip_errors=True`.

### Added

- `unstd.yaml`, behind `unstd[yaml]`, provides safe YAML 1.2 native-value
  loading, multi-document streams, and comment-preserving parse/edit/write.
  Duplicate keys and unknown tags are rejected; parser instances and scalar
  semantics are isolated from optional C extensions. The base install keeps
  zero third-party dependencies.
- `unstd.compress.gzipx`, behind `unstd[compress]`, accelerates gzip file and
  one-shot operations with zlib-ng, with exact stdlib bindings on a base
  install. Streams retain stdlib framing, ownership, modes, seeking and error
  families; one-shot defaults follow the running interpreter. Compressed bytes
  may differ between codecs while recovered content remains identical.
- NDJSON readers and writers accept a reusable `structs.Codec` through `codec=`.
  Readers decode each row directly into its validated schema, preserving
  physical line numbers and schema paths on errors. `on_error` reports each
  rejected record once before the normal raise or explicit skip. Untyped
  streaming still works on a base install, and typed processing reuses the
  existing serde extra.
- Base64 exposes stdlib-compatible `b64encode`/`b64decode`, standard and URL-safe
  functions, MIME byte helpers and `encode_file`/`decode_file` stream helpers
  alongside the convenience API. Standard defaults, alternative alphabets,
  buffer handling, exception types and diagnostics follow the running stdlib.
  Acceleration is limited to plain-byte encoding and strict ASCII decoding;
  other cases and native decode failures use stdlib.
- Eleven executable parser/token fuzz targets use independent oracles,
  authenticated synthetic seed corpora, finite pytest replay, deterministic
  mutations, and isolated Atheris campaigns. Workers preserve exact failures
  and enforce wall/input deadlines, POSIX CPU limits and Linux address-space
  limits. CI runs bounded pull-request campaigns and deeper nightly coverage
  campaigns, retaining reports and generated corpora. Harness canaries verify
  that incorrect results, unexpected exceptions, crashes and exhausted budgets
  fail the run.
- Recorded chaos campaigns cover mixed filesystem/process histories,
  handshaken writer interruption, stream failures and clock/entropy outages.
  Bounded installed/fallback workers, seed replay, exact fault journals, strict
  completion controls and Linux/macOS pull-request/nightly CI campaigns retain
  recovery evidence. Ordinary correctness discovery stays separate from
  explicit chaos runs.
- Recorded concurrency campaigns cover atomic filesystem histories, UUID
  contention/fork safety, and shared clone/regex/codec/YAML/map isolation.
  Independent models, broken-result controls, bounded installed/stdlib workers,
  lane-specific replay and Linux/macOS pull-request/nightly jobs retain
  schedules and observations. This explicit lane stays outside default pytest;
  owning defect regressions remain in ordinary validation.

### Changed

- Native JSON acceleration now requires bounded eligibility proofs. Encoding
  snapshots at most 16 exact builtin values before calling orjson, retaining
  exact whitespace and escaping; other inputs use cached or direct stdlib
  encoders. Decoding verifies the complete backend result and falls back to
  stdlib for every float, including lossy large-integer conversions.
  Hooks/custom codecs bypass speculation; deep or unproven cases and backend
  errors retry the original input with stdlib. Callback isolation, snapshot
  mutation, DEL escaping and near-limit recursion failures have generated
  contracts that also assert real backend use.
- JSON type annotations accept duck-typed writers and typed `default`
  callbacks. The `numpy=True` conversion extension is consistent across
  backends. With an explicit custom `cls`, `dump`/`dumps` forward all
  constructor keywords unchanged. Differential tests use the running stdlib as
  the authority across Python 3.12–3.14 and optional-backend/base configurations;
  generated cases include large integers, non-finite values, lone surrogates,
  mixed keys, formatting options, binary encodings and malformed input.
- The test suite organizes Python coverage into module-owned contracts
  under `tests/suites/product`, cross-cutting assurance under
  `tests/suites/assurance`, shared support, and standalone tooling. A validated
  inventory distinguishes active coverage from planned lanes and provides
  `python -m tests.tooling list|check|run`. CI checks inventory drift; pytest
  uses strict markers and shared generation profiles. Existing TLS policy tests
  run without the BLAKE3 extra. The coverage plan maps remaining property, fuzz,
  penetration, fault, installation, concurrency, resource and performance work
  without counting scaffolding as implemented tests. Wall-clock benchmark
  calibration assertions use the explicit `perf` marker, outside ordinary
  correctness runs.
- P0 assurance builds and installs real artifacts in pristine consumers, checks
  independent optional-extra installations, performs real loopback TLS
  handshakes, injects filesystem/process failures, and challenges parser and
  token boundaries with independent controls and bounded workers. Required CI
  execution fails when declared extras are unavailable.

### Fixed

- Installed-extra checks use locked artifact URLs and hashes for offline
  consumers, including source-built dependencies at declared floors. Crash
  controls allow Python 3.14 diagnostics to finish while retaining the short
  intentional-hang deadline.
- YAML fuzzing compares scalar `ValueError` exceptions from malformed dates
  with the reference parser, retaining exact error types and messages. The
  discovered invalid-date input is preserved in the authenticated seed corpus.
- URL convenience Base64 decoding behaves the same with and without pybase64:
  ignored characters no longer interfere with missing-padding repair,
  completed padding preserves stdlib's handling of trailing data, non-ASCII
  text raises stdlib's `ValueError`, and strided decoding buffers work. URL
  decoding no longer emits pybase64's alternate-alphabet warnings. Strict
  validation still accepts nonzero unused pad bits, as stdlib does; it does not
  promise canonical spelling. Independent differential and generated
  malformed-input contracts cover native and fallback paths. The decode
  benchmark compares the same strict validation mode on both paths.
- Process timeouts must be positive and finite. Filesystem rollback preserves
  scratch-descriptor ownership and the original error, and directory syncing
  remains best effort. Bearer-token envelopes require strict canonical
  Base64url spelling, and non-ASCII MACs are rejected. The general-purpose URL
  Base64 codec retains its documented lenient behavior. Editable YAML supports
  standard `!!str` construction.
- Native WAV writes select the WAV format for file-like, extensionless and
  misleading-extension outputs in both one-shot and streaming writers, pinned
  by independent audio-extra consumer tests.
- Confirmed surrogate-region regex and truncated PCM frame findings remain as
  module-owned regressions and corpus entries. Faithful regex recovery is
  selected before exposing lazy match projections; incomplete PCM frames are
  rejected explicitly.
- `proc.run` kills and reaps its direct child before propagating cancellation,
  including the `KeyboardInterrupt` path where `subprocess.run` could return
  with an unreaped child. Captured output, timeout behavior and callback
  semantics are preserved; real SIGINT regressions cover `run`, `capture` and
  `run_ok`, including recovery.
- The Python UUIDv7 fallback resets its lock and counter state after POSIX fork
  so a child can generate IDs even when a vanished parent thread held the
  counter lock. The parent's state and selected UUID backend are preserved;
  a bounded ordinary regression and controlled concurrency histories pin the
  deadlock.
- Concurrent regular-expression cache misses no longer exceed the documented
  512-entry limit when multiple threads select the same oldest entry. Compiles
  publish before trimming and retry lost evictions. A bounded regression forces
  the duplicate-oldest interleaving against native and stdlib backends.
- Package metadata lookup is deferred until `unstd.__version__` is requested
  and the result is cached. Importing submodules such as
  `unstd.serde.structs` avoids that startup cost while retaining the same native
  codecs, parsing behavior and metadata-derived version, including the
  uninstalled-source fallback.

### Performance

- Reduce JSON eligibility and compact-encoding overhead, accelerate URL-safe
  Base64 decoding, and improve structural cloning and stdlib array packing.
  The portable Levenshtein fallback evaluates packed dynamic-programming
  columns. Public call signatures, serialized formats and optional dependencies
  for these hot-path optimizations stay the same; parity and regression tests
  cover the optimized boundaries.
- Reduce serde dispatch and allocation overhead for default JSON encoding,
  small decoded JSON values, integer snapshot proofs, short Base64 decoding
  and NDJSON writer setup. These optimizations preserve public signatures,
  codec policies, output and error semantics. A repeatable complete function
  profile includes output-checked native/fallback benchmarks and explicit API
  comparisons.
- Reduce duplicate private-attribute traversal when cloning Pydantic models
  and the dataclass conversion entry check. Remove redundant integer
  conversion, branching and bit assembly in the portable UUIDv7 generator.
  Existing APIs, direct stdlib aliases, secure entropy and synchronized counter
  behavior remain. Custom dataclass constructor arguments and msgspec copy
  reducers defer to the stdlib copy protocol. Reproducible per-function
  clone/UUID profiles and paired workload timings include output validation,
  unchanged-signature checks and explicit coverage gaps.
- JSON benchmarks separately measure fallback records and eligible
  text/integer cases. Python 3.13.14 measured 5.98× text encoding and 1.91×
  integer-array decoding. The old speed floors covered incompatible behavior
  and were reset; no uniform speedup is promised.

## [2.1.1] - 2026-10-04

### Fixed

We keep Python's Unicode classification, scoped flags, empty-match iteration and
nullable capture behavior across native engine upgrades. Eligible ASCII and byte
work still run natively; surrogate recovery and typed native refusals preserve
outputs without retrying replacement callbacks.

## [2.1.0] - 2026-10-04

### Added

- `Codec(decimal_format=...)` accepts a callable over each `Decimal`, so exact
  decimal policies stay exact instead of passing through a float. Callback
  errors propagate, and the same encoder remains usable afterward. This uses
  [msgspec 0.22's encoder support](https://github.com/msgspec/msgspec/releases/tag/0.22.0).

### Changed

- We refreshed the optional-backend and development lock, including uuid-utils
  1.0 and msgspec 0.22. Existing UUID generation and codec defaults keep their
  behavior; the serde extra's msgspec floor moves to 0.22.
- We quarantine new resolver and isolated-build releases for two days. CI uses
  the reviewed lock and checksum-backed uv versions from the pinned
  [setup-uv 10.2 action](https://github.com/astral-sh/setup-uv/releases/tag/v10.2.0).
  The separate floor job still proves the oldest supported direct dependencies.

### Fixed

- We keep each main commit's CI run in its own concurrency group. Newer pushes
  cannot replace pending checks for a release commit, and the release tag must
  agree with the package version before publication.

## [2.0.0] - 2026-09-27

### Breaking

This is a 2.0 surface pass: every change below renames or removes a public name,
and each old name fails at import rather than changing meaning in place.

- `serde.b64` is one verb per direction. `b64s` / `b64u_s` → `encode(data, url=,
  pad=)`, `b64d` / `b64u_d` → `decode(s, url=)`, `b64text` / `b64u_text` →
  `decode_text`, `b64_json` / `unb64_json` → `encode_json` / `decode_json`.
  `pad=False` is new - the unpadded base64url JWTs and bearer tokens use, which
  callers were `rstrip("=")`-ing by hand - and url-safe `decode` now accepts
  padded and unpadded input alike. Standard `decode` stays strict.
- `ids.hash` is gone; it was three second names for `crypto.digest`. Content
  hashes are `digest.hex(data)`, short ids `digest.hex(data, n)`, raw bytes
  `digest.raw(data)` (was `sum_` / `hex_` / `hex_n` / `content_hash`).
  `digest.of_parts(parts)` is `digest.Stream(parts).raw()`; `Stream.update_all`
  went with it, and `Stream.sum_` / `hex_` are `raw` / `hex(n=None)`.
  `digest.KDF_BYTES` folded into `DIGEST_BYTES` (both were 32).
- `crypto.token.mac` / `mac_bytes` → `mac_hex` / `mac_raw`, so the return type is
  in the name as it is in `digest`. `verify_mac` takes a tag in either form.
  `equal` lives in `token` only, and `token.opaque` is
  `rand.crypto.token_urlsafe` - it was a pass-through.
- `time.dateutil` is `time.zoned`, holding only what needs `whenever`:
  `wall` / `parse` / `now` / `instant` / `to_utc` (were `zoned_wall` /
  `parse_zoned` / `zoned_now` / `parse_instant` / `zoned_to_utc`). The pure-stdlib
  half - `parse_iso`, `parse_utc`, `parse_date`, `from_epoch_s`, `from_epoch_ms` -
  moved to `timeutil`, so none of it needs the `time` extra any more.
- `timeutil.span(start, end=None, *, unit, assume_utc=False)` replaces
  `span_hours`, `span_minutes`, `age_days`, `minutes_until`, `minutes_since`, and
  `hours_between`. Those five disagreed about whether a floating stamp was UTC
  depending on which unit you asked in; `span` is strict unless told otherwise.
  `iso_range`, `lookback`, `days_range`, and `day_offset` are removed.
- `timeutil.iso(dt=None, *, ms=False)` replaces `utcnow_iso`, `utcnow_iso_ms`,
  and `iso_fmt`; `today(utc=True)` replaces `utcnow_date_iso`; `today_iso` and
  `date_fmt` are removed (`.isoformat()`); `human(tz="")` replaces `human_now`
  and `human_now_tz`.
- `timeutil.parse_date` returns `None` on bad input like every other parser
  here, instead of raising.
- `serde.structs.codec(...)` is removed; construct `structs.Codec(...)`.
- `audio.wavx` drops the stutter: `read_wav` / `write_wav` / `read_blocks` /
  `WavWriter` → `read` / `write` / `blocks` / `Writer`.
- `fs.read_bytes`, `fs.read_text`, and `fs.iter_dir` are removed. They measured
  slower than the `pathlib` methods they wrapped.
- `proc.TimeoutError` is removed. It aliased `subprocess.TimeoutExpired`, which
  does not subclass the builtin of that name.
- `iters.take(iterable, n)` takes the iterable first, like `chunked`, `windowed`,
  and `nth` beside it. The old order fails loudly (`'int' object is not
  iterable`), never silently.
- `iters.batched_with_key` is `iters.runs` - it groups consecutive runs by key;
  it never batched. Its module is `iters.consecutive`.
- `iters.partition(iterable, pred)` takes the iterable first, like every other
  combinator in `iters`. The old order raises `TypeError` at the call.
- `serde.ndjson.read(fp)` / `write(fp, rows)` → `load(fp)` / `dump(rows, fp)`,
  the same shape as `jsonx.dump(obj, fp)` and `toml.dump(obj, fp)`. `dump` still
  returns the record count.
- `timeutil.now_tz(tz)` requires its zone. Called bare it was a second spelling
  of `utcnow()`.
- `toml`'s write half is `tomlkit`'s own functions rather than wrappers around
  them, so `dumps` / `dump` accept `sort_keys=` and each name carries tomlkit's
  types. On a base install each is a placeholder that raises naming the extra,
  decided at import rather than re-checked per call.
- `unstd.crypto` no longer re-exports the `blake3` class; it was the backend,
  not a surface. The guard moved into `digest` / `token`, so `crypto.tls` - pure
  stdlib - now imports on a base install instead of failing on a missing BLAKE3.
- `crypto.tls.client_context(certfile=...)` alone loads the chain, which is the
  combined cert-plus-key PEM most bundles ship as; it used to be ignored and the
  client dialed without mTLS. A `keyfile` with no `certfile` raises `ValueError`.
- The `ids` extra no longer pulls `blake3`; nothing under `ids` has used it since
  `ids.hash` folded into `crypto.digest`. Depend on `unstd[crypto]` for digests.
- `unstd.containers.guard` is gone. Its placeholder and `toml`'s copy of it are
  one private helper, and every missing-backend name now raises the same
  `unstd.<module>.<name> requires the '<extra>' extra` message.

### Performance

- `jsonx.dumps` 416 → 264 ns, `dumpb` 375 → 200 ns, `loads` 251 → 212 ns on a
  small dict (raw orjson: ~180 ns). The fast path no longer detours through a
  checking wrapper, and `loads` names its return type with an annotation instead
  of a runtime `typing.cast` call.
- `ndjson.dumpb` / binary `write` let orjson append each newline
  (`OPT_APPEND_NEWLINE`) instead of concatenating a second `bytes` per row.
- `rand`'s scalar draws are stdlib `random` on every install. Routing
  `random()` / `choice` / `shuffle` through numpy's `Generator` one value at a
  time was 5-7x slower than the stdlib draw; numpy still fills the bulk arrays.
- `timeutil.wall` / `mono` / `mono_ns` / `perf` and `rand.crypto.*` are the stdlib
  functions bound directly, with no forwarding frame.
- `timeutil.iso` formats through `isoformat` (~2.6x faster than `strftime`), and
  `epoch_ms` is exact integer math on `time_ns()`.
- `structs.Codec.encode` / `decode` are msgspec's own bound methods.
- `digest.hex(data, n)` asks the XOF for `n` bytes rather than slicing a full
  hex string, and checks the width inline.
- `jsonx.canonical` reuses one prebuilt encoder instead of letting `json.dumps`
  construct a fresh one per call: ~1.25 µs → ~0.9 µs on a small dict, same bytes.
- `b64.encode(url=True)` rides pybase64's `altchars` straight to `str`: 293 →
  192 ns for 64 bytes unpadded.
- `token.mac_hex` / `mac_raw` check the key inline - 943 → 674 ns - and `verify`
  follows (2.1 → 1.6 µs).
- `ndjson.loads` parses a clean blob in one comprehension over the backend
  decoder and strips a line only when a direct parse fails: 338 → 187 µs per
  1k records, level with a bare orjson loop. A bad line still re-walks the slow
  path, so the error and its line number are unchanged.
- `iters.windowed(step=1)` is staggered `tee` views zipped together, a C-level
  walk: 4x on 10k items. `unique_everseen` skips the key call when there is no
  key, and `last` indexes exact builtin sequences without an ABC probe (2x).
- Pure forwarders are now the function they forwarded to: `timeutil.utcnow`
  (`partial(datetime.now, UTC)`, 132 → 96 ns), `uid.parse` / `uid.derive`
  (`UUID` / `uuid5`), `iters.flatten` (`chain.from_iterable`, 2x), `zoned.now`
  (`ZonedDateTime.now`), and - with the `text` extra - `fuzz.ratio` /
  `distance` / `jaro_winkler` as the rapidfuzz kernels (~25-40% per call). Clock
  freezers that patch at the C layer (`time-machine`) still see the bound
  references. The pure-Python fallbacks are held to the kernels by a property
  test, since with the extra installed nothing else runs them. The fuzz scorers
  take their two strings positionally.
- `wavx.wrap_pcm` packs the 44-byte header with one `struct` call - byte-identical
  to `wave`, pinned across widths, channel counts, and partial frames - 2.7x
  faster on 32 KB.
- `iters.partition` is the current `itertools` recipe - verdicts teed beside the
  values and split with `compress` - so `pred` runs once per element instead of
  once per side: 533 → 459 µs on 10k items with a trivial predicate, and half the
  predicate calls whatever it costs.
- `ndjson.dumps` joins orjson's row bytes and decodes once, and a text-mode
  `dump` decodes each row's bytes rather than re-entering `jsonx.dumps`: ~20%
  on both (399 → 317 µs and 547 → 421 µs per 1k rows).
- `clone.asdict` tests dict keys and values for atoms inline instead of paying a
  call per leaf: 5.2 → 3.8 µs on a dict-heavy record.
- `rand.choices` unboxes the drawn index array once instead of calling `int()`
  per element: 379 → 209 µs for 10k draws.

### Fixed

- `text.fuzz.best_match` / `extract` / `cdist` keep a choice scoring exactly at
  `score_cutoff`, as documented. rapidfuzz's own cutoff drops boundary pairs
  through a float round-trip (about 15% of random short pairs on 1.1), so every
  fast path now scores in full and applies `>=` itself - at no measurable cost.
  `cdist` also returns float64, so its scores equal `ratio`'s exactly.
- `fs.atomic_write` / `atomic_writer` no longer flip the process-wide umask to
  read it when creating a new file. Setting it to 0 and back left a window in
  which any other thread creating a file got mode `0o666`/`0o777`. The temp file
  is now opened at `0o666` so the kernel applies the umask, and an existing
  destination's mode is still copied over.

- `rex` accepts its own compiled patterns. On the irgx path `rex.compile(p)` and
  `rex.search(p, s)` raised `TypeError` for a `p` this module had just returned,
  because only stdlib's `re.Pattern` was recognized; stdlib takes either.
- `rex.purge` clears irgx's pattern cache too, not just stdlib's.
- A pattern irgx declines (backreferences, lookaround) is no longer re-attempted
  on every call. irgx caches what it compiles, not what it refuses, so each
  `rex.search(r"(\w+)\s+\1", ...)` paid a full compile attempt plus a raise -
  about 1.8 ms, against 0.2 µs for stdlib.

### Changed

- `rex` keeps a bounded front cache like stdlib's `re._cache`, and hands flags to
  irgx as its keyword arguments instead of building an inline `(?imsx-u)` prefix
  through `RegexFlag` arithmetic. A module-level `rex.search` drops from ~5 µs to
  ~0.8 µs - now twice as fast as `re.search` - and a compiled pattern's
  `.pattern` is the caller's source again rather than carrying the prefix.
- `rex.findall` rewrites irgx's `None` for an absent group only when one is
  actually present, found by a C-level containment scan instead of rebuilding
  every row. A two-group `findall` over 10 KB drops from ~940 µs to ~240 µs.
- The `rex` extra requires `irregex>=2.6.0`. Below it, a replacement template's
  octal escapes (`\07`, `\101`) and unknown non-letter escapes rendered different
  text than stdlib with no error; 2.6.0 reads them the way `re` does. It also
  makes a group-reading `rex.sub` and a grouped `rex.split` one call into the
  engine instead of one per match, and stops a capped `subn`/`split` walking past
  its cap - on 10 KB, templated `sub` goes from ~7x slower than `re.sub` to
  about even, and `subn(..., count=5)` from ~130x to ~1.7x.

## [1.1.0] - 2026-09-27

### Fixed

- `clone.deep` returns the types it was given. Its msgspec round-trip decoded a
  nested `StrEnum` as `str`, an `IntEnum` as `int`, and an `OrderedDict` or
  `defaultdict` as a plain `dict` - losing the factory with it - and the equality
  check meant to catch a lossy round-trip passed every one, because those values
  compare equal to what came back. `deep` is now an exact-type walk that never
  leaves Python objects, so there is nothing to lose.
- `python3 -m bench update --group X` no longer erases every other group's floors.
  A narrowed run now merges into the baseline instead of replacing it.

### Changed

- `clone.deep` is pure stdlib and faster where callers actually spend time:
  4.1x over `copy.deepcopy` on a small dict (was 1.2x, slower than deepcopy on
  CPython 3.14), 2.2x on pydantic models (was a failed encode then deepcopy), and
  4.4x on large JSON trees (was 5.8x on CPython 3.13 - the price of the fix above;
  on 3.14 the two tie). Dataclasses, pydantic models, and `msgspec.Struct` are
  rebuilt the way `deepcopy` rebuilds them, with the walker standing in for its
  recursion; any other node is handed to `deepcopy` on its own while its siblings
  keep walking. The `clone` extra no longer installs anything and stays declared
  so existing `unstd[clone]` requirements resolve without a warning.

### Added

- `clone.asdict`: `dataclasses.asdict`'s result on the same walker, 10x faster on
  a dataclass tree with `datetime` leaves. A custom `dict_factory`, a
  non-dataclass argument, or a namedtuple / container subclass in the tree hands
  the call to the stdlib, so the answer is always the stdlib's.

## [1.0.5] - 2026-09-01

### Changed

- Prose and fixtures name generic things rather than one deployment's. The
  NDJSON docstring described the format by pointing at two artifacts nobody
  outside their origin has ever seen, and a crypto fixture put a private service
  in a JWT audience - so the docstring names what NDJSON is actually used for,
  and the fixture says `example` like its sibling suite already did. No code
  paths move.

## [1.0.4] - 2026-08-31

### Fixed

- The extra floors sit at the minor series rather than the patch we happened to
  develop against, and CI now proves them. 1.0.3 relaxed `==` to `>=` but left
  each floor on an exact patch, so `unstd[model]` still rejected an application
  one pydantic bugfix behind - a resolver error that reads as the application's
  fault. The API a backend offers changes at the minor, which is where a floor
  belongs.

### Added

- A `floors` CI job resolves `--resolution lowest-direct` and runs the whole
  suite against the oldest version every floor admits. A floor is a promise that
  the library works that far back, and nothing was testing it: the rest of the
  matrix resolves the newest of everything, so the floors could have been fiction
  indefinitely and only a downstream with an older pin would have found out.

## [1.0.3] - 2026-08-31

### Fixed

- Every extra declares a floor rather than an exact `==` pin. The pins were
  inherited from the monorepo this package was extracted from, where an exact
  pin is correct because a monorepo is the application. Published, they meant an
  application already on `pydantic==2.13.4` could not install `unstd[model]` at
  all - the resolver reports the conflict against the application, not against
  us - and two libraries pinning patches are simply not co-installable. The
  floors are the versions each extra is tested against; a consumer that wants an
  exact version has its own manifest and lockfile to say so in.

## [1.0.2] - 2026-08-31

### Fixed

- The package ships `py.typed`. Every surface here is annotated and CI has been
  running `mypy --strict` over all of it, and none of that reached a consumer:
  PEP 561 says to ignore the annotations of a package with no marker, so a
  strict downstream saw `jsonx.dumps` as `Any` and wrote a suppression per call
  site to get its build green.

## [1.0.1] - 2026-08-31

### Added

- `tls.client_context` takes a `minimum_version`, defaulting to the same TLS 1.3
  it always asserted, and `tls.LEGACY_MINIMUM_VERSION` names the 1.2 floor for a
  peer not yet shown to negotiate 1.3. Without it, a service that dials one such
  peer had to hand-roll a second context — which is where the parameter this
  module refuses to have gets added. Anything below 1.2 raises instead of
  building a context.

### Fixed

- `unstd.__version__` is read from install metadata instead of being a literal
  beside the one in `pyproject.toml`. The two drifted on the first patch release
  and the gate caught its own package's version, so the tag was cut and nothing
  shipped; there is now one place to edit.

## [1.0.0] - 2026-08-31

### Added

- First public release of the full library: serde, ids, crypto, time, model,
  rex, containers, rand, pack, clone, text, audio, toml, proc, fs, and iters.
  Base install stays pure stdlib; every accelerated backend is an optional extra.
- `crypto.tls` — the verifying TLS client context, with no parameter for
  skipping verification, split out of the module that used to hold it. Its mTLS
  and pinned-root tests now actually run: they mint a throwaway self-signed
  certificate, which needs `cryptography`, which nothing had declared, so all
  three had been skipping rather than passing.
- `digest.legacy_blake2b` — the one deliberate exception to "one digest",
  for an identifier already defined in terms of BLAKE2b that cannot change
  algorithm without renaming every value derived from it. Keeping that case
  inside the seam is what stops it becoming a reason to import `hashlib` next
  door and grow a second digest for new work too.
- `ruff` and `mypy --strict` now run in CI, and `release.yml` calls the CI
  workflow as a gate rather than keeping its own copy of one — so the bar a tag
  clears is the bar a pull request clears, by construction. Neither tool had ever
  run over this package; the first pass found 26 type errors.
- A `proto` extra. `timeutil`'s protobuf `Timestamp` bridge imported
  `google.protobuf` with nothing declaring it, so the two `proto_timestamp`
  functions raised `ModuleNotFoundError` on any install that had not happened to
  pick protobuf up sideways. The import is guarded now and names the extra.
- `bench` — a harness behind the speedup numbers the READMEs print. Each case is
  one accelerated call against the stdlib call it stands in for, reported as a
  ratio, with `bench/baseline.json` pinning a floor per case that `update` may
  raise and never lower. It refuses to grade a missing backend as a pass, and
  refuses to report a ratio between two functions that disagree on the answer.
- The suite runs on a base install. It could not before: four surfaces have no
  stdlib fallback and raise at import, which pytest reports as a collection
  error, so `pip install unstd && pytest` aborted before running anything — and
  the guarded-fallback paths, which are the reason the library exists, had never
  been exercised by anything. `tests/conftest.py` now decides collectability by
  probing importability, and the fallback CI leg runs the whole suite (477 tests)
  instead of a hand-maintained subset.
- Tests for `ndjson`, which had none and 0% coverage; for `timeutil`, which had
  none; and for the package's own description of itself — the top-level docstring
  listed four module groups while sixteen shipped, so the prose, the README table
  and the directory listing are now held to each other, along with the
  zero-dependency claim.

### Changed

- `crypto` no longer projects an external algorithm table. The widths it
  enforces are BLAKE3's own — 32 bytes of digest, 32 of key, 32 of tag — so they
  live beside the calls they bound (`digest.DIGEST_BYTES` / `KDF_BYTES`,
  `token.KEY_BYTES` / `TAG_BYTES`) instead of being imported from a generated
  registry. `derive_key` takes any context string and says plainly that
  registering one is the caller's job; nothing about the derivation changed.
- The crypto known-answer vectors are now the BLAKE3 team's own published
  `test_vectors.json`, vendored verbatim and attributed in NOTICE. All three
  modes — plain hash, keyed hash (the MAC), and `derive_key` — are checked
  against the authors' answers across 35 input lengths spanning the chunk and
  subtree boundaries, where the previous fixture carried seven hand-picked rows.
- `rex` is now backed by `irregex`
  (`import irgx`) instead of `google-re2`; the extra installs `irregex>=2.3.0`.
  The surface, the stdlib-`re` fidelity contract, and the degrade-to-`re`
  fallback for backreference/lookaround patterns are unchanged.
- The linear-time guarantee now covers three families that previously degraded to
  backtracking `re`: `re.VERBOSE`, `re.ASCII`, and `bytes` patterns. `bytes`
  patterns compile byte-oriented, so `rb"\w+"` stops at `caf` in `café` exactly
  as stdlib does.
- Two divergences that were always true of `rex` are now documented and pinned by
  tests rather than left to be discovered: `$` matches the absolute end of the
  text (not the position before a trailing newline, which is Perl's and stdlib's
  rule), and POSIX bracket expressions like `[[:alpha:]]` are a real class to the
  linear engine but a nested-set typo to stdlib.

### Fixed

- Nine `rex` signatures annotated their `pattern` and `string` parameters as
  `DOTALL` — the regex flag — instead of `S`, the type parameter they declare.
  Ruff's `FURB167` rewrites `re.S` to `re.DOTALL`, marks the fix safe, and had
  spliced its replacement over every PEP 695 `S` in the file. The rule is now
  disabled here, with the reason written down next to it.
- `timeutil.human_now_tz` ignored the timezone it was passed. It built the right
  instant and then called `.astimezone()` with no argument, which converts back
  to local time, so every caller got local time under another zone's name.
- `timeutil.iso_fmt` stamped a literal `Z` on whatever it was handed. A
  `datetime` in any other zone came out labelled UTC while still carrying its
  original wall-clock digits — a silent offset, in the format used for the wire.
  Aware datetimes are converted to UTC first now; naive ones are relabelled.
- `timeutil`'s human format string used `%-d` and `%-I`, which are a glibc/BSD
  extension. `strftime` raises `ValueError` on both under Windows, so every
  human-facing timestamp in the module was a hard failure on one of the three
  platforms the package claims to support.
- A `bytearray` or `memoryview` pattern now raises `TypeError`, matching what
  stdlib `re` does with one. The backends accept any buffer as a pattern, so
  `rex` was quietly accepting patterns its own twin refuses — being a superset is
  a divergence too, and code written against it broke the moment the extra was
  absent and the `re` fallback took over.
- `sub` and `subn` now type a callable `repl` as receiving either backend's match
  object, which is what has always happened at runtime. The two are
  interchangeable for everything a replacement function reads (`group`, `groups`,
  `groupdict`, `span`, `expand`, `start`, `end`); only `.pos` and `.endpos` are
  stdlib-only.
- `findall` is typed `list[S | tuple[S, ...]]` rather than a union of two list
  types, so the element type survives instead of collapsing (typeshed spells the
  same fact `list[Any]`). Runtime results are unchanged.

## [0.0.1] - 2026-08-31

Parked a good name.

### Added

- Name reservation on PyPI.
