# Randomness and identifier backend audit

2026-10-22 America/Los_Angeles. This directory contains an exploratory, read-only production audit. No production files, test expectations, dependency specifications, lockfiles, or pre-existing dirty changes were modified. The only new files are in this directory. No dependencies were installed.

**Decision: retain current defaults.** PCG64DXSM is the strongest future explicit opt-in for heavily parallel simulations; SFC64 deserves a large-batch throughput experiment. Neither is a transparent replacement: seeded output changes, and this machine's timing series do not establish a no-regression result. The existing security randomness aliases and UUID public types should stay intact.

## Evidence and reproducibility

- `random-measured-source.txt` produced the recorded 113-workload run; `probe_random.py` is the formatted, typed retained driver. Both exercise 113 workloads, including the actual internal wrappers with interchangeable engines, shapes 1/16/1,024/100,000, weighted/unweighted selection, scalar controls, engine initialization, public seed/constructor costs, secure primitives, UUID minting/derivation/parsing, fresh-process imports, and shallow engine sizes.
- `random-results.json` retains every timing sample, calibration iteration count, spread, environment, loaded-source hashes, import observations, and engine state/size snapshot.
- `random-provenance.json` records SHA-256 hashes of the byte-exact measured driver and the retained driver. The retained version adds CLI/typing and replaces default-argument timing lambdas with `functools.partial`; that can change fixed call overhead, so the recorded timings must be attributed to `random-measured-source.txt`, not the retained source. No new timing run was made.
- `random-contracts.json` retains a focused constructor call observation and UUID type/alias checks.
- Run the retained driver from the repository root with `.venv/bin/python bench/math-audit/probe_random.py --output /tmp/math-random-rerun.json` only when other CPU-heavy work is stopped. `--validate-only` checks the setup without timing or writing results. Choose a new output to preserve the original observations; do not target repository benchmark baselines. The retained driver is `bench/math-audit/probe_random.py` and requires the explicit `--output` CLI.

Environment: CPython 3.13.16, NumPy 2.5.3, uuid-utils 1.0.0, macOS 26.6 arm64. Each workload was calibrated to at least 3 ms and measured in five rotating/interleaved rounds with GC temporarily disabled and then restored. No profiler was active. These are single-machine exploratory microbenchmarks, not deployment workload measurements.

**Timing qualification:** 111 of 113 cases had a range exceeding 25% of their median, with some ranges exceeding 1,000%. Raw medians contain implausibly inconsistent ordering between tiny operations. They must not be used to assert specific speedups or a no-overhead result. No retries were used to discard slow measurements. The current driver records wall time; unlike `bench/rand_diagnose.py`, it does not separately retain thread CPU time, so the cause of instability is unresolved. A paired wall/thread-CPU rerun on a stable machine remains necessary before adopting any candidate.

Fresh-process samples included `unstd.rand` import times of 69–147 ms and peak RSS of 35.0–35.3 MB; empty-process peak RSS was 18.5–18.6 MB. `uuid_utils` imported in 1.58–4.38 ms with 18.9–19.0 MB peak RSS. These are only three observations each; RSS differences include Python/import metadata and are not an isolated allocation measurement. Importing the secure submodule initializes the `unstd.rand` package and therefore currently imports NumPy when installed. Changing to lazy initialization would move work to first use and requires its own compatibility/performance review.

Measured shallow Python object sizes (not complete retained heap/native allocations):

| Engine | Generator bytes | BitGenerator bytes | Increment vs PCG64 BitGenerator |
| --- | ---: | ---: | ---: |
| PCG64 | 224 | 176 | — |
| PCG64DXSM | 224 | 176 | 0 |
| SFC64 | 224 | 160 | -16 |
| Philox | 224 | 232 | +56 |

All four engines ship in the already imported NumPy installation. They require no additional Python dependency. Equal shallow sizes establish neither equal total memory nor equal construction or execution cost.

## Per-function assessment

Locations refer to `src/unstd/rand/draw.py`, `src/unstd/rand/crypto.py`, and `src/unstd/ids/uid.py`.

| Function | Current backend and behavior | Candidate, overhead and decision |
| --- | --- | --- |
| `rand.random` | Direct bound C-backed `random.Random.random`, MT19937 float | Keep. NumPy scalar dispatch has extra fixed cost; seed/stream parity is pinned. None of the simulation engines is suitable for secrets. |
| `rand.randint` | Direct stdlib method, inclusive bounds, rejection-based bounded selection | Keep. NumPy returns a different scalar type, has int64 bounds and changes the stream. |
| `rand.uniform` | Direct stdlib method over MT19937 | Keep scalar backend and floating-point endpoint behavior. NumPy scalar call does not provide a demonstrated no-overhead replacement. |
| `rand.choice` | Direct stdlib method over bounded MT19937 selection | Keep. NumPy conversion/dispatch for a Python sequence adds work and can change item representation. |
| `rand.shuffle` | Direct stdlib in-place method | Keep. NumPy has a different algorithm/stream and sequence behavior. Existing benchmark exercises only 32 items; arbitrary sizes need separate validation. |
| `rand.sample` | Direct stdlib sampling without replacement, `counts` supported | Keep. NumPy `choice(replace=False)` is not a faithful replacement for the full `counts` API or seeded histories. |
| `rand.seed` | Construct PCG64 replacement, seed stdlib, then publish replacement | Retain atomic-validation order. DXSM/SFC64 require explicit new engine identity. Reinitialization/construction costs must be measured, not hidden behind long bulk batches. |
| `_size` | `operator.index`, then nonnegative check | Keep. Necessary admission semantics before engine dispatch; no numeric backend work to substitute. |
| `_floats` / `rand.floats` | NumPy PCG64 `uniform` ndarray fill, or stdlib list loop; validates finite bounds/span | DXSM/SFC64 research candidates for large arrays; no default swap. Preserve ndarray vs list, limits, invalid-input behavior, endpoint rounding and exact current seeded streams. |
| `_ints` / `rand.ints` | NumPy PCG64 `integers`, default int64; stdlib arbitrary-int list fallback | Same candidate decision as floats; test both small and full-width intervals and endpoint handling. Changing dtype would alter range and memory semantics. |
| `_choices` / `rand.choices` | Materialize items; optional NumPy weights conversion/sum/normalization; generate indices, `.tolist()`, map to original items | Keep current default. Engine-only improvements cannot remove normalization and Python object materialization. Precomputing tables or probabilities introduces lifecycle/memory work and stale-input risks. Weighted and unweighted paths must be measured separately. |
| `Generator.__init__` | Explicit stdlib default; optional NumPy bulk backend; independent stdlib scalar state | Keep backend selection. A deterministic observation shows `Random.seed(None)` followed by `Random.seed(173)` for both public backends: initialization performs entropy seeding before immediately reseeding. Removing duplicate setup is a separate promising implementation optimization, but it must preserve subclass `self.seed` dispatch, error order and validation atomicity. No production edit made. |
| `Generator.backend` | Property returning stored string | Keep; engine selection happens at initialization, no backend work per access. |
| `Generator.seed` | Replacement PCG64 allocation before stdlib reseed; replaces private state | Keep; same reproducibility/atomicity constraints as module seed. |
| `Generator.random` | Python method forwarding to instance stdlib random | Keep. Independent-state contract costs one wrapper call; rebinding per instance trades allocation/state for dispatch and is not free. |
| `Generator.randint` | Python method forwarding stdlib inclusive integer method | Keep bounds, state consumption and stdlib numeric semantics. |
| `Generator.uniform` | Python method forwarding stdlib | Keep scalar backend. |
| `Generator.choice` | Python method forwarding stdlib | Keep sequence and scalar semantics. |
| `Generator.shuffle` | Python method forwarding stdlib | Keep mutation and seeded histories. |
| `Generator.sample` | Python method forwarding stdlib, including `counts` | Keep complete API. |
| `Generator.floats` | Forwards its selected engine to `_floats` | DXSM/SFC64 could only be new explicit identities, with construction/first-call costs included. |
| `Generator.ints` | Forwards its selected engine to `_ints` | Same decision and contract as module bulk ints. |
| `Generator.choices` | Forwards its selected engine to `_choices` | Same decision and materialization limitations as module choices. |
| `crypto.token_bytes` | Exact `secrets.token_bytes` alias, OS CSPRNG bytes | Keep. Substituting NumPy/MT is insecure. Direct `os.urandom` would change defaults and alias identity, save only wrapper work and keep the same entropy source. |
| `crypto.token_hex` | Exact `secrets.token_hex` alias | Keep secure entropy and native hex conversion. User-space caches/DRBGs add state, fork/reseed obligations and failure-policy changes. |
| `crypto.token_urlsafe` | Exact `secrets.token_urlsafe` alias | Keep CSPRNG plus URL-safe Base64 semantics. Encoding is part of requested output cost. |
| `crypto.below` | Exact `secrets.randbelow` alias, unbiased rejection sampling | Keep. A modulo shortcut can bias output; generic native random alternatives require unbiased range and failure parity proof. |
| `crypto.bits` | Exact `secrets.randbits` alias | Keep OS entropy and integer behavior, including zero bits. Security canaries prove live entropy failures propagate. |
| `crypto.choice` | Exact `secrets.choice` alias | Keep unbiased secure selection and sequence behavior. |
| `_seed_counter` | Fresh 2-byte `os.urandom`, bound `int.from_bytes`, 12-bit mask | Keep CSPRNG; caching entropy changes state/fork/failure behavior. |
| `_next_ms_and_counter` | Thread lock reserves monotonic timestamp/counter, reseeds on new ms or overflow | Keep lock, rollback and overflow behavior. Removing synchronization is not a compatible throughput optimization. Reservation and return ordering can differ under overlap. |
| `_reset_after_fork` | Fresh lock and cleared fallback state | Keep essential process isolation. Native uuid-utils has its own registered RNG reseed hook. |
| `_stdlib_uuid7` | Timestamp plus 12-bit counter and fresh 62-bit CSPRNG tail; stdlib UUID object | Keep portable fallback. Native minting is already selected when available; CPython 3.14 UUIDv7 is already selected when native package absent. Retain fresh entropy and portable UUID construction. |
| `uid.new` | One selected `uuid7()` then `str` | Keep existing Rust-backed default when extra installed. Native UUID object is internal; public canonical string stays stable. No extra dispatch framework needed. |
| `uid.new_hex` | One selected `uuid7()` then `.hex` | Keep. Switching to stdlib-returning compat UUID introduces additional object construction without public benefit here. |
| `uid.parse` | Exact alias of stdlib `uuid.UUID` | Keep full constructor signature, return class, exceptions and identity. Native parsing may be faster but changes public type and compatibility. |
| `uid.derive` | Exact alias of stdlib `uuid.uuid5` | Keep persisted SHA-1 UUIDv5 byte values and stdlib return type. Native `uuid5` needs native namespace objects; compat converts namespace/results. Neither is an alias-compatible swap. Replacing hash with SHA-256/BLAKE3 changes every derived identity. |

## Research findings and primary sources

1. **PCG64DXSM is the closest algorithm candidate.** NumPy documents a statistical issue with millions of long parallel PCG64 streams; a single stream or thousands of streams are not a practical affected scenario. DXSM keeps jump/advance capabilities and strengthens the output permutation. This is statistical quality, not a CSPRNG security upgrade. Our shallow-size measurement matches PCG64, but constructor and distribution throughput must still pass a stable performance gate. [NumPy upgrade rationale](https://numpy.org/doc/stable/reference/random/upgrading-pcg64.html), [PCG64DXSM state/guarantees](https://numpy.org/doc/stable/reference/random/bit_generators/pcg64dxsm.html).

2. **SFC64 is a throughput specialist; Philox is a stream-management specialist.** NumPy recommends PCG64 or DXSM for general use, says SFC64 is fast but lacks jumpability, and describes Philox as slower with convenient independent keys. Published Linux measurements favor SFC64 for many distributions, but Windows ordering differs, demonstrating why an upstream table cannot establish local no-regression. [Official performance comparison](https://numpy.org/doc/stable/reference/random/performance.html), [SFC64](https://numpy.org/doc/stable/reference/random/bit_generators/sfc64.html), [Philox](https://numpy.org/doc/stable/reference/random/bit_generators/philox.html).

3. **Seeded history is an existing contract.** Tests at `tests/suites/product/rand/unit/test_rand.py:382` and `:529` explicitly construct PCG64 references; a default algorithm change would fail real compatibility guards. NumPy guarantees stream identity under strict same-engine/build/environment/call-sequence conditions, not arbitrary engine swaps. An explicit new engine parameter avoids silently changing old outputs but adds API/documentation and constructor-branch costs. [NumPy compatibility policy](https://numpy.org/doc/stable/reference/random/compatibility.html).

4. **Simulation PRNGs cannot replace secure primitives.** Stdlib MT19937 and NumPy generators are explicitly unsuitable for secrets. The six crypto exports are already direct aliases, with zero unstd wrapper calls; tests at `tests/suites/assurance/security/structure/test_random_primitives.py` require those exact identities, verify seed immunity, and inject failure into the live entropy provider. [Python random](https://docs.python.org/3.13/library/random.html), [Python secrets](https://docs.python.org/3.13/library/secrets.html), [NumPy warning](https://numpy.org/doc/stable/reference/random/index.html).

5. **Output conversion can dominate choices.** NumPy says supplying `p` selects a more general, less efficient sampler and a different stream even for uniform probabilities. Current weights are recomputed on each call, preserving mutation semantics. Alias tables or cached probabilities would require explicit prepared-distribution objects and break-even evidence; they cannot be called free substitutions. [NumPy choice documentation](https://numpy.org/doc/stable/reference/random/generated/numpy.random.Generator.choice.html).

6. **Threads and reusable buffers are optional workload redesigns.** NumPy documents long-lived workers with independent streams and `out` arrays for certain distributions; number of threads is part of reproducibility. Worker pools, synchronization, arrays and task submission incur costs, and `uniform`/`integers` do not directly match all documented `out` examples. They are candidates only for explicitly large sustained workloads. [NumPy multithreading example](https://numpy.org/doc/stable/reference/random/multithreading.html), [parallel stream guidance](https://numpy.org/doc/stable/reference/random/parallel.html).

7. **Native UUID backend is already deployed.** Installed uuid-utils 1.0.0 declares Rust `uuid` 1.26 and `rand` 0.10.2 with `fast-rng`. Its Python package registers a child-after-fork RNG reseed; its Rust function maps explicit reseed failures to Python `OSError`. Rand documents ChaCha12, OS reseeding every 64 KiB, cached generator state, and no automatic fork reseed on its own. This explains part of the speed/security tradeoff; it does not make the backend interchangeable with stateless OS calls for all secrets. [Versioned Cargo manifest](https://raw.githubusercontent.com/aminalaee/uuid-utils/1.0.0/Cargo.toml), [versioned package fork hook](https://raw.githubusercontent.com/aminalaee/uuid-utils/1.0.0/python/uuid_utils/__init__.py), [versioned native source](https://raw.githubusercontent.com/aminalaee/uuid-utils/1.0.0/src/lib.rs), [Rand ThreadRng security](https://docs.rs/rand/0.10.2/rand/rngs/struct.ThreadRng.html).

8. **UUID security and identity constraints.** RFC 9562 says UUIDs must not act as access-granting capabilities, calls for CSPRNG/fork handling, and fixes v5 to SHA-1; stronger name hashes belong in a distinct v8 scheme. That requires a new identity namespace and migration, not an invisible backend change. Keep authentication tokens on `secrets`. [RFC 9562 §§5.5, 6.9, 8](https://www.rfc-editor.org/rfc/rfc9562.html).

9. **Documentation precision:** the comment at `src/unstd/ids/uid.py:121` calls CPython 3.14's implementation “the same” generator. Both support RFC v7 monotonic IDs, but CPython documents a 42-bit counter while this fallback uses 12 bits; their entropy allocation and performance differ. Native/stdlb paths are already selected; no new dependency is necessary. [Python 3.14 UUIDv7](https://docs.python.org/3.14/library/uuid.html).

The prior checked-in `bench/clone-ids-results.md` reports native canonical text around 160 ns versus fallback around 2.39 µs on a different interpreter/date, and deliberately preserves exact `parse`/`derive` identities. Treat that as historical supporting evidence, not a current speed guarantee.

## Acceptance gate before any future swap

A candidate needs unchanged valid/invalid-input behavior, seeded histories or an explicitly new stream identity, item/array/scalar types, crypto identity and entropy-failure canaries, UUID vectors, concurrency/fork/clock tests, and stable paired measurements including construction, reseeding, first/cold use, small/medium/large calls, full output conversion and retained memory. Existing `bench/rand_diagnose.py` retains wall/thread-CPU pairs and frozen ceilings for secure bits, seed and shuffle and must not be weakened. Reject candidates with a new dependency, lazy first-call penalty, extra retained buffers or dispatch work unless the user explicitly accepts that tradeoff. No finite benchmark establishes a universal “in no way” no-overhead proof.

Scoped validation of the retained driver passed Ruff, Ruff formatting, strict project mypy (`--follow-imports=silent`), and its 113-workload `--validate-only` pass. The source hash for the recorded run is `9e54d5c27f1024973fc51beb7b205b7708485e51883f9e1fefb433163494d69c`.
