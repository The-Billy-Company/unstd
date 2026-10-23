# Mathematical backend evaluation

Reviewed October 22, 2026, America/Los_Angeles.

**Historical audit:** the findings and source identities below describe the
pre-implementation checkout. The subsequent [implementation and qualification
record](implementation.md) documents the production changes, patched audio
build, experimental compression options, and final validation. Retained raw
audit measurements were not rewritten to describe newer code.

**Retain the runtime defaults. No researched replacement currently establishes
all three requested properties: higher throughput, better security, and no
added overhead for every existing operation.** This audit changes only research
and benchmark artifacts. It introduces no runtime dependency, function wrapper,
backend dispatcher, import, thread pool, or production-code change.

There are useful narrower opportunities. Resolve the provenance of the loaded
libsndfile security patch first. Qualify a PCM16 fallback simplification, an
explicit PCG64DXSM simulation engine, and the existing native inflater boundary
with zlib-rs. Keep these separate: a faster simulation generator is not a
cryptographic upgrade, and a memory-safe implementation is not automatically
faster or immune to resource exhaustion.

## Coverage and evidence

The audit inventories **221 exported callable entries, including locally owned
methods/properties, across all 18 packages**, tied to **226 existing benchmark
workloads**. Nine entries are typing/enum/result vocabulary with existing
explicit exclusions. Reexports are collapsed by the repository's existing
public-API discovery. Inherited methods of exposed third-party classes remain
owned by those classes' documented contracts.

- [Per-operation decisions](functions.md): every discovered entry, its provider,
  decision, and security/compatibility boundary.
- [Machine-readable inventory](inventory.json): the same map, workload notes,
  runtime/library identities, loaded-extension hashes, source fingerprints and
  **691 lexical arithmetic candidates**. The operator scan deliberately also
  finds string repetition, overloaded paths, and type expressions; it is a
  completeness aid, not a claim that all 691 expressions are numerical work.
- [Random/UUID investigation](random.md), [numeric/audio/time investigation](numeric.md),
  and [crypto/compression investigation](crypto.md): detailed function-level
  research and primary sources.
- New raw observations: [113 random/UUID workloads](random-results.json),
  [36 numeric comparisons](numeric-results.json), and
  [13 text comparisons](text-results.json). These are exploratory host-local
  measurements, not portable speed guarantees.

Repository code was inspected for arithmetic, numeric conversion, bit operations,
hashing, checksums, distance recurrences, random distributions, ordering/counting,
calendar conversion, and finite-value validation. Native C/Rust boundaries and
vendored compression versions were inspected as well. There is no matrix
multiplication, FFT, BLAS/LAPACK computation, symbolic algebra, or general-purpose
high-precision numerical API here. Replacing MKL/OpenBLAS/Accelerate would not
accelerate these code paths. Transitive libraries are mapped to their actual
providers; this is not a new line-by-line audit of every dependency's internals.

The existing checkout already contained crypto hardening and benchmark work.
Those changes and their historical results are credited as existing work, not
changes made by this audit. A captured content manifest checks 579 existing
production/native/test/configuration/documentation files for preservation;
concurrent commits can change Git identity without changing those bytes.
The final capture records concurrent workflow/manifest changes separately;
the audited source/native mathematical implementations remained unchanged.

## What is actually running

Observed runtime: CPython 3.13.16, macOS arm64, NumPy 2.5.3, RapidFuzz 3.14.6,
uuid-utils 1.0.0, blake3 1.0.10, SoundFile 0.14.0, whenever 0.10.5,
immutables 0.21, and sortedcontainers 2.4.0. These observations describe this
environment, not every supported wheel.

| Area | Active provider and material distinction |
| --- | --- |
| Simulation randomness | stdlib MT19937 for scalar operations; NumPy PCG64 for bulk operations |
| Secure randomness | Exact `secrets` aliases using OS entropy; importing its package still initializes installed NumPy |
| UUIDs | Native uuid-utils minting; exact stdlib UUID parsing/UUIDv5 derivation |
| Text scoring | Direct RapidFuzz bindings with the extra; bundled C/Python base-install paths |
| Audio | SoundFile/CFFI with **bundled libsndfile 1.2.2**; NumPy/wave and array/wave are fallback tiers |
| Binary arrays | NumPy buffer views/conversion; scalar `struct` APIs are already direct C-backed aliases |
| Hash/MAC | Official Rust BLAKE3 binding; BLAKE2b uses CPython `_blake2`; comparison uses `_hashlib` |
| TLS | Python `ssl` linked to **OpenSSL 3.5.9**; cipher/KEX are negotiated beyond this configuration helper |
| Gzip | Python zlib-ng 1.0.0 bundles **zlib-ng 2.2.5**. `_gzip_native` is absent here; streamed reads use zlib-ng and one-shot decompression uses stdlib |
| Other native gzip builds | Source contains libdeflate 1.25 CRC/whole-buffer decoding and an inflater using macOS system zlib or vendored zlib 1.3.2 elsewhere; source presence is not evidence of loaded execution |
| Clock/calendar/container APIs | Native stdlib clocks/datetime, whenever Rust extension, C immutables HAMT, and segmented Python sortedcontainers |

The stdlib zlib reports 1.2.12 on this Apple runtime. That string does not prove
missing downstream security patches. In particular, CVE-2022-37434 requires
`inflateGetHeader`, which the inspected CPython/unstd gzip paths do not call;
the existing review also found Apple's documented fix. Do not label all gzip
operations vulnerable from that version string. See [the scoped analysis](crypto.md).

## Security priority: audio build provenance

The loaded libsndfile reports the version named in **CVE-2026-88386**. The CVE
record describes a misaligned write in WAV format-chunk parsing that can cause
denial of service. The upstream fix uses `memcpy` for unaligned field stores.
The record's CISA assessment is medium 5.5 with availability impact.
[CVE record](https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2026/88xxx/CVE-2026-88386.json),
[maintainer issue](https://github.com/libsndfile/libsndfile/issues/1150),
[upstream fix](https://github.com/libsndfile/libsndfile/commit/474e4d328b1e6240b93ec6ed14efb7c6a44bee57).

**Unresolved:** the exact bundled binary's patch provenance and host
exploitability. Installed metadata identifies the bundled dylib but does not
establish a backport. No malicious native fixture was executed. Do not present
this as a proven exploitable deployment or a clean security bill.

Obtain a maintained build containing the fix and retain the existing SoundFile
API. Benchmark that binary against the original before any no-overhead claim.
Changing only a Python requirement or installing a patched system library may
not replace the loaded library: SoundFile tries its bundled library first.
[SoundFile loading policy](https://python-soundfile.readthedocs.io/en/latest/).

## Candidate decisions

| Candidate | Potential benefit | Cost/contract obstacle | Decision |
| --- | --- | --- | --- |
| Patched libsndfile build, same SoundFile API | Addresses the reviewed parser issue | Exact binary provenance and before/after performance remain unverified | Highest security follow-up; no Python dispatcher needed |
| Owned PCM16-to-int16 copy in NumPy fallback | Eliminates unnecessary float conversion, scaling, clipping and rounding | Fallback-only; supported-version/platform and complete streaming checks remain | Strongest narrow measured speed candidate |
| Explicit NumPy PCG64DXSM | Better statistical behavior for very many parallel streams; no new dependency | Changes seeded output; constructor/tiny/distribution costs unqualified | New explicit engine identity only; retain current default |
| zlib-rs under existing C ABI | Smaller manual-memory surface; credible native throughput project | FFI/allocator/error contracts, Rust build costs, cold/RSS/public throughput unmeasured | Isolated native experiment; retain current provider |
| libdeflate 1.26 | Upstream CRC selection improvements for Arm Neoverse V-series | Not an established Apple Silicon improvement; no advertised security fix; C11 floor | Targeted native dependency qualification |
| SFC64 / Philox | Large-array speed / independently keyed simulation streams | Different histories and stream controls; Philox state grows; timings unstable | Specialized explicit APIs only |
| Rust bundled text prototype | Checked-slice numerical core | Existing short-Jaro regressions and 7.73x extension-size increase | Retain C default |
| ISA-L, Zstandard/LZ4/Brotli | Useful specialized compression throughput | Gzip levels/flush/seek or wire format differ | Not transparent replacements |
| Numba/NumExpr/CuPy | Compilation, expression fusion, or GPU acceleration on suitable workloads | New runtime/dependencies, startup/setup, transfers, output and precision changes | Reject default substitution here |

Primary qualification sources: [PCG64DXSM rationale](https://numpy.org/doc/stable/reference/random/upgrading-pcg64.html),
[NumPy generator comparison](https://numpy.org/doc/stable/reference/random/performance.html),
[zlib-rs project](https://trifectatech.org/projects/zlib-rs/),
[libdeflate 1.26](https://github.com/ebiggers/libdeflate/releases/tag/v1.26),
[python-isal API limits](https://github.com/pycompression/python-isal).
The linked subreports distinguish upstream claims from this repository's measurements.

## Measurements that constrain the decision

### Audio and packing

An owned PCM16 copy was compared with the existing NumPy fallback, including
complete BytesIO WAV parsing. Seven alternating pairs retained wall and thread
CPU samples. Median paired **CPU speedup** for full reads was 2.23x at one
sample, 2.12x at 16, 2.54x at 1,024, and 13.34x at 262,144. Kernel-only ratios
were larger. These are observations, not confidence bounds. This does not
accelerate the currently selected SoundFile route.

The prototype preserves owned writable storage instead of returning an aliased
buffer. Correctness checks covered every PCM8/PCM16 code, 100,020 seeded/boundary
PCM32 codes, three dtypes, 1/2/4 channels, empty input, malformed frame sizes and
unsupported widths. Its broader float32 shortcut slowed tiny full reads to
0.92x/0.96x baseline speed, so reject that blanket change. Complete fallback
stream lifetimes and cross-platform timing remain unqualified.

The existing portable `struct` helper was 1.3–3.2x faster for tested Python
float lists, but much slower for ndarray inputs, and its integer coercion and
supported dtypes differ. Both packing helpers return owned bytes. On decoding,
the NumPy result is a view and the portable result is a fresh Python list.
Keep the existing explicit portable and NumPy APIs. `numpy.frombuffer` already avoids a decode copy;
mandatory copies would add cost and alter its view contract.
[NumPy buffer ownership](https://numpy.org/doc/stable/reference/generated/numpy.frombuffer.html).

### Randomness and UUIDs

The 113-workload exploration includes engine constructors, seeding, weighted
choices, scalar controls, sizes 1/16/1,024/100,000, secure randomness, UUIDs,
cold imports and shallow state size. **111/113 series exceeded 25% spread
relative to their median.** Do not quote these medians as established wins.
The cause of instability was not resolved by this wall-only probe.

PCG64 and PCG64DXSM each measured 176 bytes for their shallow BitGenerator
object, SFC64 160 and Philox 232; Generator objects were 224 each. These are
not total heap/native-state measurements. Equal shallow size does not establish
equal memory, setup or throughput.

PCG64 histories are explicitly pinned by existing tests. Weighted normalization,
index-to-Python-object conversion, RNG construction and repeated seeding can
dominate the primitive. All six security exports already have exact `secrets`
identity; a cache or simulation generator would change security/state contracts.
Native UUID minting is already enabled with its extra. Preserve exact stdlib
`parse` and `derive`, including UUIDv5's persisted SHA-1 identity. UUIDs are not
access-granting capabilities.
[NumPy stream policy](https://numpy.org/doc/stable/reference/random/compatibility.html),
[Python secrets](https://docs.python.org/3.13/library/secrets.html),
[UUID standard](https://www.rfc-editor.org/rfc/rfc9562.html).

Two non-swap follow-ups were found: Generator construction seeds from entropy
before applying the requested seed, and a UUID fallback comment calls its
12-bit-counter construction the same as CPython's documented 42-bit-counter
implementation. Neither production behavior nor documentation was changed here.

### Text scoring

RapidFuzz already supplies the optional native scorers and batch kernels.
The 13 new comparisons verify exact result types and binary64 score bits before
and after timing. They compare installed RapidFuzz against base-install
wrappers, and complete public `cdist` with one versus two workers. Keep all
raw samples and their noise flags; thread CPU excludes work in other worker
threads and therefore cannot measure total parallel CPU consumption.

Two workers impose substantial setup cost on tiny matrices; larger matrices
can benefit. This supports retaining `workers=1` and explicit caller selection,
not universal parallelism. Float32 or uint8 matrices reduce space but change
precision/cutoff outcomes; the current float64 intermediate alone needs
`8 * queries * choices` bytes before its mask and Python-list output.
[RapidFuzz matrix/worker contracts](https://rapidfuzz.github.io/RapidFuzz/Usage/process.html).

The earlier [C-versus-Rust experiment](../rust-text-results.md), on CPython
3.13.14, recorded a 0.72 Rust/C ratio for 64-code-point Levenshtein, but
1.17–1.33 for several short Jaro cases and a 51,488-to-398,192-byte extension
increase. Those are earlier measurements, not reruns here. Safe Rust improves
the manual-memory boundary, while that implementation fails the requested
cost constraint.

Retain algorithm identities: `difflib_ratio` is order-sensitive SequenceMatcher
with autojunk; Indel uses an LCS-derived metric; Levenshtein has unit edit costs;
Jaro-Winkler has its threshold/prefix rule. Different metrics cannot be treated
as interchangeable accelerators. StringZilla has both byte and UTF-8-aware
distances, but a Python replacement must also preserve code points, lone
surrogates, arbitrary sequences and conversion costs. No installed StringZilla
or Levenshtein alternative was benchmarked here.
[difflib semantics](https://docs.python.org/3.13/library/difflib.html),
[Indel definition](https://rapidfuzz.github.io/RapidFuzz/Usage/distance/Indel.html),
[Levenshtein definition](https://rapidfuzz.github.io/RapidFuzz/Usage/distance/Levenshtein.html),
[StringZilla Unicode distinctions](https://github.com/ashvardanian/StringZilla).

### Hashes, authentication, TLS and checksums

Retain official Rust BLAKE3, CPython BLAKE2b, OS entropy and OpenSSL-backed
constant-time comparison. SHA/BLAKE2/xxHash substitutions change existing
identities or security properties. BLAKE3 derive-key mode is not a password KDF.
The previous experimental C binding ignored requested worker counts and rejected
NUL-containing KDF contexts, despite passing its vector set.
[BLAKE3 binding](https://github.com/oconnor663/blake3-py),
[KDF contract](https://docs.rs/blake3/latest/blake3/fn.derive_key.html),
[comparison guarantee](https://docs.python.org/3.13/library/hmac.html).

Existing research already records native canonical token parsing gains,
explicit verifier setup/RSS costs, and 5–11 ns extra work on some cheap Python
rejects. Earlier parallel-hash measurements include raw speedups below one
around 1–2 MiB and tiny stream-dispatch costs. Keep these losses visible;
large-buffer gains cannot establish no overhead on tiny inputs or concurrent
requests. See [existing implementation evidence](../crypto-audit/fixes.md) and
[the full review](crypto.md).

The current OpenSSL 3.5.9 meets the reviewed September 29 branch fix level.
Keep runtime patch qualification outside request paths. Caller-owned configured
SSLContext reuse can avoid repeated setup without changing the provider;
mutable global context caches can cross trust policies. Rustls/AWS-LC requires
a new transport binding and API/error/trust-root qualification. Post-quantum
key shares add wire bytes, so cannot satisfy a blanket unchanged-overhead claim.
[OpenSSL advisory](https://openssl-library.org/news/secadv/20260929.txt),
[SSLContext sharing contract](https://docs.python.org/3.13/library/ssl.html#ssl.SSLContext),
[Rustls provider boundary](https://docs.rs/rustls/latest/rustls/#cryptography-providers).

Gzip needs IEEE CRC32, not the CRC32C polynomial used by some hardware
instructions. A CRC is corruption detection, not authentication. libdeflate is
not a streaming decoder; inflate substitutions must preserve bounded reads,
concatenation, seek, error delivery and fallback work. Rust memory safety does
not eliminate decompression bombs or stack/resource bugs; zlib-rs has a patched
historical malicious-input stack-overflow advisory. Its current release is not
in that advisory's affected range.
[libdeflate scope](https://github.com/ebiggers/libdeflate),
[zlib-rs advisory](https://github.com/trifectatechfoundation/zlib-rs/security/advisories/GHSA-j3px-q95c-9683).

## Remaining numeric and adjacent functions

| Functions | Assessment |
| --- | --- |
| `timeutil` clocks, epoch, span, parse, protobuf bridges | Already use native stdlib clocks/datetime with exact integer millisecond logic where required. Retain negative-epoch/DST/range/callback semantics. ciso8601 accepts a different input language and does not transparently replace historical second-level offsets. |
| `zoned` classes and conversions | whenever is already native Rust. Converting stdlib-returning APIs into these types adds adapters and changes identity. Retain the explicit separate API. |
| All iterator counting/window/partition/distinct helpers | Preserve laziness, consumption order, unhashable equality and buffer growth. These are generic object operations; NumPy materialization changes semantics and cost. |
| SortedList/SortedSet/SortedDict/Map | Already directly expose segmented sorted containers and a C persistent HAMT. A new tree or copied dict changes class APIs, persistence or workload complexity. |
| JSON/NDJSON/typed codecs and numeric hooks | Existing reusable msgspec codecs already amortize setup. Preserve arbitrary integers, finite/nonfinite rules, signed zero, exact canonical bytes, Decimal policy and user float hooks. |
| Base64 | Existing pybase64/libbase64 SIMD path and small strict-decode threshold already address native throughput. Preserve lenient/strict padding and alternate-alphabet behavior. |
| TOML/YAML | Numeric parsing is tied to dialect and round-trip semantics; YAML intentionally uses pure Python for stable scalar resolution. A C loader is not a transparent safe replacement. |
| Models | Pydantic uses Rust pydantic-core already; msgspec is a different model/validation API. Preserve validators, numeric coercion and settings semantics. |
| Clone/filesystem/process | No standalone mathematical backend to replace; process timeout/duration uses native finite checks/clocks. Preserve graph hooks, durability and shell/timeout policy. |

JSON deserves particular caution: orjson's documented handling of NaN/infinity,
64-bit integers and native float32 encoding differs from stdlib behavior.
Existing guards/fallbacks are intentional. UltraJSON's maintainers describe
maintenance-only status and encourage orjson; it is not a security-motivated
replacement for this stack. Never disable interpreter integer-conversion limits
to make a numerical benchmark look faster.
[orjson numeric contracts](https://github.com/ijl/orjson),
[stdlib integer limits](https://docs.python.org/3.13/library/json.html),
[UltraJSON maintainer policy](https://github.com/ultrajson/ultrajson),
[msgspec codec reuse](https://github.com/msgspec/msgspec/blob/main/docs/perf-tips.rst).

NumPy already selects SIMD kernels using CPU features at import. General JIT,
GPU, or big-integer substitutions would add setup, conversions, dependencies,
or different numeric types. Numba documents first-call compilation and fastmath
relaxation of IEEE rules; CuPy documents context/kernel setup. GMP-based integers
offer large-value speed but add native types and documented allocation-failure
risks. None is a justified blanket backend here.
[NumPy SIMD dispatch](https://numpy.org/doc/stable/reference/simd/index.html),
[Numba compilation](https://numba.readthedocs.io/en/stable/user/jit.html),
[Numba fastmath](https://numba.readthedocs.io/en/stable/user/performance-tips.html),
[CuPy overheads](https://docs.cupy.dev/en/stable/user_guide/performance.html),
[gmpy2 boundary](https://gmpy2.readthedocs.io/en/stable/overview.html).

## Acceptance and limits

A future swap must first retain outputs, types, widths, rounding, byte formats,
seed histories, error/validation order, mutation/ownership, callbacks and required
security properties. Test independent vectors and malformed inputs before timing.
Benchmark complete public calls at empty/tiny/default/bulk sizes and crossover
neighbors, with alternating paired wall/CPU samples and untouched controls.
Measure cold import, construction, first use, memory/allocation, binary/build
size and concurrent aggregate throughput separately. Use the deployment CPU and
every supported release platform/runtime; rerun when the code or provider changes.

Do not hide conversions or setup outside timing if a caller pays them, select
only favorable shapes, silently change algorithms, introduce per-call version
checks, or weaken frozen ceilings. Existing 30% benchmark headroom is a useful
regression alarm, **not proof of zero overhead**. No finite benchmark proves
the literal absence of overhead for all possible inputs and machines.

Under the strict requirement in this request, unqualified candidates stay out
of production. This audit therefore adds no runtime cost. It identifies exactly
which narrower experiments can make progress and what evidence still prevents
their adoption.

## Validation performed

- Existing `bench.speeds check`: all 226 workloads, 18 packages and nine
  vocabulary exclusions passed coverage and correctness checks.
- Existing benchmark gate suite: **190 passed, two deselected**.
- New maintained probes: Ruff, formatting and mypy passed for all three files;
  random validation checked 113 workloads and numeric verification passed its
  27 combinations plus exhaustive/boundary/invalid-input probes.
- The text run checked 13 paired comparisons before/after timing and retained
  seven noisy comparisons. Source/binary hashes are in its raw JSON. The
  random/numeric timed source snapshots and post-measurement driver changes
  have separate provenance records; their old timings are not attributed to
  the polished callback wrappers.

No new production backend, full release-platform matrix, network TLS performance
campaign, native-library rebuild, or binary vulnerability reproduction was
performed. Those are candidate-qualification limits, not passing results.
