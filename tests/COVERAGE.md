# Coverage plan

This is the implementation backlog for a library that promises stdlib fidelity,
optional native acceleration, safe boundary helpers, and a zero-dependency base
install. It describes evidence to build, not a claim that every lane already
exists. The selected packaging, TLS, filesystem/process, and parser/token P0
modules are implemented; [P0.md](P0.md) maps their executable evidence.
Parser/token fuzz targets, authenticated retained corpora, finite replay and
bounded deterministic/Atheris campaigns are also implemented; the
[fuzz runbook](tooling/fuzz/README.md) maps their commands and evidence limits.
The suite registry owns executable selection; a planned lane must remain
visibly planned until it contains meaningful tests and its runner is verified.

## Organization and evidence

The organization follows Billy's [test architecture](../../billy/services/ai/tests/README.md),
[product and assurance split](../../billy/services/ai/tests/suites/README.md),
[support boundary](../../billy/services/ai/tests/support/README.md), and
[standalone tooling boundary](../../billy/services/ai/tests/tooling/README.md).
Billy's service-specific evals, provider infrastructure, and browser machinery do
not become unstd requirements merely because the directory organization matches.

```text
tests/
├── suites/
│   ├── product/<module>/<kind>/
│   └── assurance/<risk>/
├── support/
└── tooling/
```

One module owns a behavior: mirror `src/unstd/<module>` under
`suites/product/<module>`, then choose the evidence kind. A risk genuinely spans
modules: place it under `suites/assurance/<risk>`. Reusable generators, assertions,
fixtures, reference artifacts, path anchors, and worker helpers belong in
`support`. Standalone fuzz and mutation campaigns belong in `tooling`. Tests of
those instruments belong in suites.

A test has one canonical home. A Hypothesis test embedded in a legacy unit file
does not stop being property evidence, but moving that file does not make every
function in it a property. Split mixed files when building the corresponding
lane. Use markers or inventory metadata for secondary concerns instead of
duplicating a scenario under multiple paths.

| Registry lane | Current state | Evidence and ownership |
| --- | --- | --- |
| `unit` | Active, existing tests | Public outcomes, state, errors, and boundaries owned by a module. Several inherited files also contain properties or integrations awaiting finer separation. |
| `parity` | Active | Explicit stdlib JSON, Base64 and regex differential suites. Additional parity assertions remain inside unit and property files. |
| `properties` | Active, existing tests | Generated serde, pack, TOML, clone, UUID, and audio laws. Other generated tests remain in inherited files. |
| `security-contracts` | Active, existing tests | Existing crypto adverse contracts, including TLS context configuration. Reference-vector cases remain in the inherited crypto unit files. This is not a complete penetration campaign. |
| `gates` | Active | Package description, optional dependencies, typing marker, benchmark-harness integrity, fuzz registry/corpus authentication, target oracles, recorded chaos/concurrency schedules, lane/replay selection and bounded worker failure handling. Isolated artifact installation belongs to `packaging`. |
| `regression` | Active | Confirmed regex surrogate/cache capacity, UUID fork-lock, partial WAV frame and interrupted-child reaping defects, with direct assertions owned by the affected module. |
| `integration` | Active | Real filesystem/process fault schedules, interruption, cleanup and recovery. Broader application scenarios remain separately planned. |
| `pentest` | Active | Real loopback TLS trust/identity/protocol/mTLS probes with authenticated controls and bounded fixture PKI. Module-owned parser/token probes live under security contracts. |
| `packaging` | Active | Offline artifact rebuilds, pristine base and each-extra consumers, complete module probes, installed typing and negative controls. |
| `system-integration` | Planned | Broader cross-module application scenarios beyond the implemented filesystem/process boundaries. |
| `security-structure` | Planned | Early structural canaries and security-policy checks. Behavioral attack tests carry the security guarantee. |
| `chaos` | Active | Seeded mixed filesystem/process histories, barrier-selected writer kills, stream interruptions and clock/entropy provider outages; independent state/progress/ownership/recovery assertions and bounded installed/stdlib campaigns. |
| `concurrency` | Active | Bounded thread/process atomic-file histories, exact fallback UUID reservation models and fork safety, shared clone/regex caches, msgspec/YAML isolation and immutable-map branches; observed histories, corruption controls and installed/stdlib campaigns. |
| `resources` | Planned | Time, memory, descriptors, handles, scratch files, and cache bounds in controlled workers. |
| `performance` | Planned | Reproducible throughput/latency evidence with audited semantics and stable baselines. The existing `bench` program remains the current instrument. |
| `fuzz-replay` | Active | Authenticated reviewed parser/token inputs replay real oracles in eleven installed and eight import-blocked stdlib workers during ordinary validation. Exact case counts, bounds and normal exit are required. |

Further module-owned `regression` leaves are added as defects are confirmed; cross-module
integration and artifact installation under `assurance/planes/{integration,packaging}`;
behavioral and structural security under `assurance/security/{pentest,structure}`;
interruption, concurrency, resources and corpus replay under
`assurance/adversarial/{chaos,concurrency,resources,fuzz_replay}`; and measured
performance under `assurance/evals/performance`. A module-local integration still
belongs with its owning product. The inventory distinguishes implemented destinations from reserved future
coverage. The [inventory](tooling/inventory.toml) is authoritative if selection
details change.

Property testing and fuzzing are complementary. Hypothesis explores structured
laws and shrinks failures during a test run. A fuzz campaign sustains input
mutation or generation across runs, manages a corpus, and finds crashes, hangs,
or differential mismatches. Keep ordinary tests finite; retain campaign lifecycle
and budgeting in standalone tooling.

## What already exists, and what remains unproved

The inherited suite already includes stdlib differential assertions, published
BLAKE3 reference vectors, token tampering, subprocess argument/timeout behavior,
filesystem rollback, UUID clock rollback and thread concurrency, audio backend
parity, clone type preservation, regex fallback behavior, and generated laws.
These are valuable evidence to preserve, not empty scaffolding to replace.

The initial CI configuration runs Ruff, formatting, strict mypy, all-extra tests
on Python 3.12/3.13/3.14, lowest-direct dependency tests on 3.13, and a fallback
installation on 3.13. The reorganization must preserve those gates while adding
precise selection. The following gaps require explicit follow-up:

1. **Platform delivery.** Linux CI remains the existing required matrix. Add
   macOS/Windows jobs, keeping named POSIX probes explicit. A local POSIX pass
   does not establish Windows handle-sharing or process-tree semantics.
2. **Deeper campaigns.** Standalone deterministic mutation and genuine bounded
   Atheris/libFuzzer campaigns now execute real parser/token oracles. Native
   sanitizer/instrumented builds, sustained chaos, broad resource/soak gates
   and source-mutation campaigns remain separate work.
3. **Performance execution.** `bench verify` has equivalence checks and speedup
   baselines, but CI still tests its instrument rather than enforcing speed on
   a stable worker. Wall-clock calibration remains explicitly selected `perf`.
4. **Broader product depth.** P1 module laws, state machines, platform combinations
   and whole-application scenarios remain governed by the matrix below.

The P0 timeout refusal, real TLS handshakes, independent artifact consumers,
backend admission and deterministic failure probes are implemented. Required CI
uses `--require-extras`, so missing capabilities cannot silently reduce that lane.

## Module coverage matrix

P0 protects published fidelity, dependency boundaries, and security guarantees.
P1 builds broad generated and integration evidence for the remaining surface.
P2 expands sustained campaigns and stable performance/resource measurement.
Every row records contract obligations. Implemented P0 cases are mapped in
[P0.md](P0.md); the remaining product/campaign expansion remains a backlog.

| Module and priority | Existing evidence to retain | Concrete cases to add or expand | Independent oracle or invariant |
| --- | --- | --- | --- |
| `serde.jsonx` — P0 | Encode/decode parity, hooks, errors, formatting, binary encodings, generated values | Generated option combinations; malformed grammar/bytes; large integers, non-finite floats, signed zero and surrogates; custom encoders/decoders; stream failures; deep input in bounded workers; native/fallback fuzz differential | Running interpreter's `json`, comparing types, order, exact text, error classes/locations and hook effects. `dumpb` has its own transport contract; do not require stdlib default formatting there. |
| `serde.b64` — P0 | Stdlib-named Base64/MIME/file API, backend/fallback output and error parity, alternate alphabets, padding, URL mode, buffers, UTF-8 error policies and JSON failures; generated arbitrary bytes/Unicode and transport mutations | Sustained native/fallback fuzz campaigns, large-buffer/resource bounds, and broader interpreter/platform coverage | Running `base64`/`binascii`, including exact errors and buffer behavior; explicit optional-padding extension only on the URL convenience helper. |
| `serde.ndjson` — P0 | Newline framing, blank/error lines, laziness, gzip/text/binary streams | Corruption between valid records; physical line/error locations; reader/writer/iterator faults after N records; short writes; truncated gzip; early closure; long records and stream memory | Independent physical-line walk plus stdlib `json`; concatenated records preserve values, order, and framing. |
| `serde.structs` — P1 | Raw-msgspec parity, strict/coercing decode, ordering, numeric hooks | Generated schemas and valid/invalid values; schema mismatch; Decimal/UUID/float hooks; codec reuse after hook failure; nested malformed native input | Raw `msgspec.Encoder`/`Decoder` for the same schema/options; declared type and deterministic-byte invariants. A shared backend is a wrapper oracle, not independent proof of the backend parser. |
| `crypto.digest` — P0/P1 | Published BLAKE3 vectors, modes, truncation, streaming | Arbitrary stream segmentation including empty parts; block/tree boundaries; repeated digest/update states; buffer types; key/context validation; large-stream memory | Published BLAKE3 vectors first; one-shot versus segmentation is a metamorphic law, not a replacement for those vectors. |
| `crypto.token` — P0 | Independent envelopes, full byte mutations/truncations, malformed segments, Unicode, canonical spelling and MAC delegation | Generated token mutation corpus; Unicode/alphabet/padding/width combinations; changed encoded payload with equivalent decoded bytes; prefix/truncation attempts; stable fail-closed behavior | Published keyed-hash vectors and independently constructed envelope bytes; only a whole authentic payload/tag verifies. Claims remain opaque bytes: expiration/audience authorization belongs to the caller. |
| `crypto.tls` — P0 | Context policies plus actual loopback trust, chain, SAN/wildcard, validity, EKU, mTLS and protocol handshakes | Loopback handshake success; wrong root/hostname; expired and future certificate; missing/wrong client cert; protocol downgrade rejection; explicit legacy protocol acceptance; base-only import | Actual stdlib SSL client/server handshake against independently issued synthetic certificate chains and the stated TLS policy. |
| `rex` — P0 | `re` fidelity examples, flags, groups, fallback, cache bounds and ReDoS example | Generated valid patterns/subjects; spans/captures/iteration/replacements/errors; callback exactly once and exception propagation; declined operations; Unicode/bytes corpus; malicious patterns in killable workers | Running `re` for the supported parity domain; explicit fixtures for documented divergences. Native safety requires proving native execution; fallback inherits Python backtracking risk. |
| `fs` — P0 | Public contracts plus commit-stage fault matrix, descriptor ownership/cleanup, collisions/symlinks, killed writers and reader barriers | Fault every write/flush/fsync/close/chmod/rename/open/unlink stage; scratch collisions; descriptor cleanup; reader/writer races; killed writer before/after rename; disappearing entries; permission errors; symlink escapes | Destination contains the entire old or new version; no torn write; prior contents survive pre-commit failure; OS-observed modes and artifact/descriptor counts. Define orphan-temp expectations for killed processes separately. |
| `proc` — P0 | Hostile literal argv, env/cwd/stdin, timeout validation, large dual output, partial output and reaping, signals, hooks and recovery | Documented timeout rejection; Unicode/quoted argv; cwd/env/stdin; missing executable; signal exits; concurrent stdout/stderr filling; partial output; child reaping; logging-hook failure | Real child programs emit exact argv/env/input facts and controlled exit/output. Parent process deadlines enforce a bound. Do not claim descendant-tree termination unless a policy and implementation provide it. |
| `pack.binpack` — P1 | Scalar stdlib parity, array bytes, generators and zero-copy views | All declared dtype widths/orders/aliases; min/max and overflow; float representations; ndarray strides; readonly/mutable and noncontiguous buffers; malformed dtype/partial bytes; view lifetime | `struct` for the shared numeric domain; hand-authored byte fixtures and explicit IEEE rounding/NaN comparison. Scalar aliases need identity/contract checks, not an entire second stdlib test suite. |
| `audio.wavx` — P1 | Three dependency paths, PCM quantization, mono/stereo, streaming and headers | PCM endpoints/ties; multichannel/empty/ragged data; arbitrary chunk boundaries; corrupt RIFF/chunks and truncated PCM; nonseekable sinks; writer/read/close failures; early block-iterator closure | `wave` headers and raw PCM fixtures plus quantization bounds; read/write and streaming comparisons across supported backend intersections. Native-only subtypes have a separate capability contract. |
| `clone` — P1 | Deepcopy/dataclass fidelity, mutation isolation, custom protocol, cycles, optional model/struct shapes | Generated mixed typed trees; reducer/copy-hook errors; class admission/cache churn; deep/cyclic worker limits; optional model extras/private fields | `copy.deepcopy` and `dataclasses.asdict` for the declared domain; exact types and mutation isolation. Shared aliases are intentionally cloned separately by `deep`; preserve that documented divergence. |
| `ids` — P1 | RFC layout, canonical forms, clock rollback/overflow, thread uniqueness | Stateful clock sequences; backend selection; multiprocessing/fork and inherited lock behavior; entropy errors; monotonic ordering within the documented scope | RFC 9562 fields decoded with `uuid.UUID`; deterministic UUIDv5 vectors. Random engines need not emit identical IDs; finite uniqueness smoke tests do not prove collision security. |
| `time` — P1 | UTC formatting, local human time, parsing/span, DST examples, protobuf bridge | Generated aware instants; multiple IANA transition families; fractional offsets; folds/gaps/disambiguation; date bounds; epoch negatives/overflow; fraction precision; optional bridge absent/present | Aware `datetime` arithmetic and independently chosen zone-transition fixtures; protobuf Timestamp's declared range/precision. Pin wall clocks rather than relying on narrow real-time windows. |
| `model` — P1 | Config inheritance, unknown-field rejection, aliases, frozen models/settings | Nested model/settings trees; alias conflicts; environment/dotenv precedence in isolated workers; schema `additionalProperties`; frozen models with mutable/unhashable values | Pydantic model/schema behavior with explicit equivalent configuration; fixture-defined source precedence and preservation/rejection rules. `StrictModel` does not currently imply Pydantic `strict=True`. |
| `containers` — P1 | Sorted operations, missing backends, persistent versions | Stateful insert/delete/update/range/index sequences; sorted key functions; duplicates; hash/comparison errors; mutation contexts; historical-version isolation | Small list/bisect/dict/set reference models and immutable-history snapshots. Test the wrapper contract, not every unchanged dependency feature. |
| `rand` — P1 | Seed reproducibility, bounds/shapes, membership/permutations, secure-random separation | Invalid/zero sizes; equal/reversed/extreme ranges; endpoint rules; empty inputs; zero/negative/non-finite weights; seed failure atomicity; documented per-engine state ownership | Bounds, shape, membership and multiset laws; same-seed sequence within one backend. Do not assert NumPy/stdlib identical streams or infer CSPRNG security from frequency tests. |
| `text` — P1 | Similarity endpoints, pinned difflib divergence, native/fallback distances, batch/cutoff laws | Generated Unicode and repeated strings; ties/cutoff/limit boundaries; one-shot choices; worker settings; large all-pairs allocation; portable metric-law checks | Native Indel/Jaro-Winkler/Levenshtein definitions and independent small reference calculations. Fast ratio intentionally differs from difflib on stated cases; cdist must match its own scalar path. |
| `iters` — P1 | Exact consumption, infinite-stream laziness, chunks/windows, partition and dedup properties | Stateful one-shot/raising iterators; all stride/size boundaries; mixed hashability; predicate faults; uneven partition consumption; retained-object counts | Simple finite list specifications, counting iterators, and mathematical window/group/dedup laws. `tee` buffering grows when consumers diverge; test that stated behavior instead of inventing a constant-space claim. |
| `toml` — P1 | `tomllib` read parity, generated writer round trips, missing writer backend | Malformed documents, binary/text handle requirements, read/write faults, style-preserving edits, date/time/numeric boundaries, dialect-version fixtures | Running `tomllib` for reads; exact reviewed style fixtures for edits; round trips restricted to the common read/write TOML dialect rather than silently excluding a published claim. |
| `yaml` — P0/P1 | Unit cases cover safe/unknown tags, duplicate keys, YAML 1.2 values, aliases, editable style, multi-document streams and backend absence | Generated safe-value/schema properties; hostile alias/depth/resource corpus; malformed-byte boundary fuzzing; interleaved parser isolation; stream faults and cleanup | YAML specification examples and manually reviewed values/bytes; ruamel with identical safe options for wrapper parity. Backend agreement alone does not establish safe-tag or resource guarantees. |
| Package/extras — P0 | Frozen offline artifact rebuilds and contents/metadata, pristine base/each-extra consumers, complete module inventory, installed typing controls | Installed wheel/sdist from outside checkout; contents/licenses/metadata; each extra independently; pristine base; partial dependency combinations; floor/latest resolution; consumer typing | Built artifact manifest, installed distribution metadata, real import/call outcomes, and downstream strict type-check fixtures. |

## Security threat model and penetration evidence

Attackers can influence serialized bytes/text, regex patterns and subjects,
claims tokens, subprocess argument values, filesystem entries in a fixture
directory, and the identity/protocol of a TLS peer. The library's guarantees are
limited to the public seams it owns: validation/fidelity, authentic MAC checks,
verifying transport policy, literal argv handling, atomic replacement, and
documented native-versus-fallback execution.

| Attack boundary | Required behavioral evidence | Successful control |
| --- | --- | --- |
| Parsers and native codecs | Malformed/truncated inputs reject predictably; no unexpected exception, crash, hang, or corruption. YAML unsafe tags never construct arbitrary Python objects. | Equivalent valid document parses with exact expected types/values. |
| Token authentication | Tampered payload/tag/key, malformed encodings and truncation fail closed. No claim about application authorization is inferred. | Original token returns exact authenticated bytes under the correct key. |
| TLS | Untrusted identity, hostname mismatch, certificate time failure, missing client authentication, or disallowed protocol fails the handshake. | The same local fixture succeeds with correct trust, name, identity and protocol. |
| Process boundary | Shell syntax remains literal, timeout policy holds, output paths do not deadlock, and child lifecycle is observable. | The child receives the exact intended argv and completes normally. |
| Filesystem | Default traversal avoids symlink escape/cycles; atomic writes never expose partial contents; temp collisions cannot overwrite unrelated files. | A permitted fixture path reads/writes/traverses correctly. |
| Regex and resource exhaustion | Supported native paths survive hostile patterns within stated budgets; fallback classification is explicit; parser/array/corpus probes have bounded workers. | A comparable valid native case is actually executed and matches correctly. |

Use `127.0.0.1`/`::1`, ephemeral ports, short-lived fixture certificates,
fixture-owned processes/directories, deterministic readiness signals, and a
parent deadline. Explicitly close sockets, reap workers, and clean artifacts on
every outcome. A failure-to-connect or missing dependency is not a successful
attack rejection. Required CI lanes must fail when their fixture cannot run;
only explicitly optional local capabilities may skip with a precise reason.

No deployed service, public IP, consumer account, or external network scanner is
needed to penetrate this local library's boundaries. SQL injection, browser
sessions, distributed authorization, and Kubernetes chaos have no target in the
current API. Add such evidence only when an actual product surface requires it.

Structural canaries may pin use of a constant-time primitive, guarded import, or
security configuration. They detect early drift but do not replace behavioral
verification. Timing assertions on a shared machine cannot prove constant-time
cryptographic execution, and statistical randomness tests cannot prove a secure
generator.

## Failure injection, chaos, concurrency, and resources

Start with deterministic failure injection: a fixture-owned stream fails on a
specified read/write, an OS seam raises a selected error at one commit stage, a
clock returns a known sequence, or a backend raises before or after a callback.
Assert resulting state, preserved data, exception identity, released resources,
and subsequent usability. Keep doubles small and behavior-bearing; their own
failure schedule must be testable.

Chaos adds interruption and scheduling beyond a single injected call: kill a
writer worker at a barrier, race readers with replacement, combine thread/process
operations, or interrupt a stream under a recorded schedule. It remains bounded
and reproducible. Do not add unseeded sleeps or random kills to ordinary tests.
An interrupted process may leave scratch files; distinguish that from a torn
destination. Monkeypatching `fsync` proves ordering/error handling, not survival
of a real power loss. Any future crash-durability claim needs a controlled
filesystem/VM experiment with its own environment and limitations.

The [chaos runner](tooling/chaos/README.md) now executes repeated atomic-write
and process histories, actual SIGINT/SIGKILL, stream faults and clock/entropy
schedules. Journals retain planned/observed boundaries and recovery. Fresh
installed/stdlib workers have hard parent deadlines, honest platform controls,
case accounting and process-group ownership. CI derives Linux/macOS combinations
from executable groups and retains failure evidence. A discovered cancellation
leak now has a production fix and owning regression: `proc.run` kills and reaps
its direct child before returning a `KeyboardInterrupt`.

Concurrency scenarios must state what the API promises. UUID uniqueness and
filesystem visibility differ from global deterministic random sequencing. Do
not assert thread safety for backend objects that publish no such guarantee.
Use barriers/events, controlled schedules, and independently verified histories;
repeating a race many times is supplementary evidence.

The [concurrency runner](tooling/concurrency/README.md) implements these checks
under recorded schedules, with invocation/response histories and per-actor order.
Overlap permits more than one valid completion order. Independent exact UUID
models, whole-generation file models and retained-object checks define the
accepted outcomes. A held UUID lock inherited across `fork()` and concurrent
regex evictions exceeding the cache bound have owning regressions and fixes.
The explicit Linux/macOS campaigns cover the tested interpreter/backend builds;
they do not establish free-threaded Python or arbitrary mutable-object safety.

Resource probes run in child workers with a parent wall-clock deadline, bounded
input/output, and platform-appropriate memory limits. Record peak RSS or a
documented proxy, elapsed time, descriptor/handle counts, scratch artifacts, and
cache growth. Assert asymptotic or generous absolute bounds appropriate to the
contract. Examples include constant-memory NDJSON/audio streaming, bounded rex
cache, clone class-cache churn, repeated failed writer cleanup, and expected
all-pairs text matrix allocation. Use process termination, not a thread timeout,
to contain code that may hold the GIL or loop inside a native dependency.

## Standalone campaigns

[`tooling/fuzz`](tooling/fuzz/README.md) implements eleven real targets: JSON,
Base64, NDJSON, typed msgspec codecs, bearer tokens, numeric arrays, WAV, TOML,
YAML, regex and gzip. A single registry owns backend capabilities, input-envelope
versions, limits and reviewed manifests. Seed hashes/sizes, provenance,
valid/invalid/boundary controls, source discovery and orphan files are checked
before execution. `fuzz-replay` is active ordinary finite pytest evidence over
those retained exact inputs, with installed and applicable import-blocked stdlib
workers. Missing installed capabilities fail required lanes and skip explicitly
only in optional local runs.

The standalone CLI provides `list`, `check`, `replay`, `campaign` and `reproduce`.
Its deterministic mutator has a versioned seed/case stream; the Atheris engine
runs genuine coverage-guided libFuzzer mutation in the pinned Linux x86_64
CPython-3.12 environment. Both replay reviewed seeds before campaigns and run
in bounded fresh workers. Reports retain exact active failure bytes, corpus and
harness digests, source revision, versions, counts, limits and actual control
enforcement. Coverage additions remain artifacts until reviewed; there is no
automatic source-corpus churn or automatic minimization claim.

Each target must define accepted input types, the entrypoint and backend state,
expected rejection classes, oracle, input-size/time/memory bounds, deterministic
seed/replay command, and failure classification. Catch only expected input
rejections. A catch-all `except Exception: pass` can erase the bug being sought.
Save the minimized input, reproduction command, environment/backend versions,
and outcome. Every confirmed defect gains a normal owning regression case.

A short PR corpus replay is distinct from a deeper scheduled or manual campaign.
Long campaigns must report executed cases/coverage, elapsed budget, corpus
changes and failures. Python coverage-guided instrumentation does not by itself
instrument NumPy, libsndfile, or other compiled internals; deeper native fuzzing
requires instrumented dependency builds and a separate supported environment.

`tooling/mutation` should own scoped mutation campaigns over unstd's wrapper and
policy code. Start with security/fallback branches, encoding boundaries,
comparison operators, and cleanup paths. Record killed, survived, equivalent,
uncovered, and timed-out mutants. Review survivors to improve evidence; do not
ratchet a global score before establishing meaningful scope and equivalent-mutant
handling. Never mutate installed dependencies or use mutation results as a
substitute for independent oracles.

Performance tooling should keep the existing benchmark equivalence audit,
backend-availability proof, machine/environment metadata, ratio baseline, and
noise reporting. Initially link to the existing `bench` runner instead of
creating a competing baseline system. Migrate its location only as an explicit
tooling change that preserves commands and artifacts. Tight speedup gates belong
on stable runners; broad algorithmic/resource regressions can run on shared CI.

## Platform, installation, and dependency matrix

| Dimension | Existing baseline | Required extension |
| --- | --- | --- |
| Python | 3.12, 3.13, 3.14 with all extras | Preserve all supported interpreters. Exercise base installation across that range because 3.14 selects a different UUID fallback. |
| OS | Linux CI | macOS and Windows public-contract lanes, especially filesystem, processes, time formatting, native imports and audio. Skip only a named unsupported capability, not an entire module without review. |
| Architecture | Whatever the current runner supplies | Record architecture. Add aarch64/x86_64 artifact/native-backend smoke where distribution support or claimed performance depends on it. |
| Base install | No requested extras, but test dependencies present | Build artifact, create a fresh consumer environment with unstd only, run stdlib-only smoke outside checkout, then add test tooling in a separate environment for deeper fallback contracts. |
| Extras | All extras together | Each declared extra alone, absent-backend behavior, and meaningful partial combinations: audio with/without NumPy/soundfile, text with/without NumPy/rapidfuzz, proto independent of time, and model/struct dependencies independently missing. |
| Versions | Locked versions plus lowest-direct on 3.13 | Preserve floor resolution, record resolved versions, and deliberately validate latest admissible dependencies under the release quarantine. Expand floor legs when an interpreter/backend combination exposes risk. |
| Artifacts | Source/editable checkout tests | Wheel and sdist built from the intended tree, wheel built from sdist, metadata/files/licenses/typing marker checked, isolated import and representative calls. Verify package origin resolves outside the checkout. |
| Downstream typing | Strict mypy over source and bench | A tiny consumer type-checks against the installed artifact without backend stubs being accidentally supplied by the checkout. |

Dependency absence is not faithfully proved by toggling a module boolean alone.
Use fresh processes/environments for import-time binding and selected mixed
installations. For a required backend, prove that absence raises the named
actionable extra error; for a fallback, prove real values and errors rather than
import success alone.

## Coverage measurement and adequacy

Add line and branch coverage over `src/unstd` when implementing the lanes, with
per-module and native/fallback reports, retained CI artifacts, and an explicit
inventory of skipped capabilities. Establish the measured baseline before
ratcheting floors; do not invent a global percentage in advance. Error paths,
backend selection, policy checks, and cleanup branches need direct assertions
even when an aggregate line percentage already looks high.

Use scoped mutation results to assess whether those assertions detect defects.
Track reproducible failures and skip reasons separately from coverage. A required
backend lane that never takes the native branch is incomplete even at high line
coverage. Tests should fail for a deliberately broken representative policy or
boundary, rather than merely execute its code.

## Build order and completion criteria

### P0 — implemented boundary modules

The requested packaging, TLS handshakes, filesystem/process failures and
parser/token security modules are implemented with real controls and no xfails
or placeholder test functions. See [P0.md](P0.md) and each owning suite README
for exact commands and environment requirements. The fixes pinned by those
modules include bounded process timeout validation, filesystem descriptor/error
ownership, canonical bearer envelopes and editable standard YAML strings.

The fuzz delivery adds executable parser/token targets, retained corpora,
ordinary deterministic replay and standalone deterministic/Atheris mutation.
The Atheris-discovered malformed WAV child-extent failure is retained as a named
input and direct regression assertion. Native instrumentation/sanitizer builds
remain separate future evidence: Python bytecode instrumentation does not cover
compiled internals. Real power-loss durability and broad platform proof also
require dedicated environments; process-kill evidence does not substitute for
them.

The chaos delivery activates recorded mixed-fault and recovery campaigns for
filesystem/process, streams and clock/entropy boundaries. It keeps controlled
interruption evidence distinct from physical power loss and cryptographic
strength. Seed replay, actual fault journals, explicit backend admission and
bounded Linux/macOS campaigns are implemented; broader resource/soak claims
remain separate.

### P1 — broaden product and integration evidence

1. Expand each matrix row using independent oracles and shared generated values
   on native/fallback paths. Separate intentional divergence from missing parity.
2. Add state machines for containers/clock sequences, generated mixed clone
   shapes, numeric/audio boundary corpora, iterator consumption faults, settings
   process isolation, and timezone transition families.
3. Add macOS/Windows and partial-extra jobs; complete ordinary integration,
   resource lanes and expand concurrency with explicit supported guarantees.
4. Convert verified failures into minimal regression tests and retained corpus
   entries. Split inherited mixed files only when doing so improves honest lane
   selection and preserves collection.

P1 is complete when every public module has a reviewed contract inventory,
relevant generated and real-boundary evidence, and stated dependency/platform
coverage. Raw line coverage alone is insufficient.

### P2 — sustained assurance

1. Expand scheduled/manual fuzz targets and record campaign budgets/results.
2. Run controlled interruption/chaos and repeated resource/soak campaigns using
   recorded schedules and platform-specific cleanup assertions.
3. Establish stable performance workers and reviewed regression baselines.
4. Add scoped mutation campaigns and close meaningful surviving policy mutants.
5. Add instrumented native fuzz environments or controlled crash-durability
   experiments only where their additional guarantee justifies maintaining them.

For every newly active lane, require a real scenario, an independent expectation,
a positive control where relevant, a bounded runner, a documented command, a
required/optional environment policy, and evidence that a representative broken
implementation makes it fail. Report failures and skips distinctly. No empty
suite, unexercised backend, structural canary, or benchmark-harness test should
be presented as the complete assurance it merely prepares.
