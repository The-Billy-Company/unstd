# Crypto backend evaluation

Reviewed October 22, 2026, America/Los_Angeles.

This is the original investigation snapshot. The subsequent
[implemented fixes and measured candidates](fixes.md) record runtime patching,
the dependency floor, bounded verification, explicit parser preparation and
accepted/rejected optimization experiments. Statements below about unchanged
production code describe the initial audit only.

**Retain the current production backends. No investigated replacement establishes
better security and throughput with no added overhead across the supported
contracts.** The immediate security action is updating the OpenSSL linked into
the Python runtime. The strongest performance opportunity is revisiting fresh
BLAKE3 worker pools under realistic concurrency, rather than replacing BLAKE3.

This investigation adds a development benchmark and research artifacts only.
Production code, dependency declarations, lockfiles, cryptographic algorithms,
wire formats, and backend dispatch are unchanged. It introduces no work into
production calls. Existing unrelated working-tree changes were left intact.

## Evidence and scope

The inventory covers all 21 callable operations in `crypto.digest`,
`crypto.token`, `crypto.tls`, and `rand.crypto`, plus UUID hashing/randomness and
filesystem temporary-file entropy. Internal constructors, truncation,
worker selection, native chunk absorption, and token Base64 were also reviewed.
There are no standalone encryption, public-key signing, password hashing, or
key-storage APIs in this crypto seam to replace.

Evidence is split into:

- [Digest/backend research](hash-research.md): official Rust/C implementations,
  versions, native batching, supply-chain history, compatibility, and build costs.
- [Token, RNG, and UUID research](token-rng-research.md): function decisions,
  constant-time comparison, entropy, token parsing, and identity contracts.
- [TLS research](tls-research.md): runtime patching, alternatives, actual context
  compatibility, upstream performance studies, and certificate security.
- [Measured backend experiments](backend-results.json): 91 output-checked
  comparisons with every batch sample, source hashes, versions, and noise flags;
  18 current crypto workload baselines and six exact RNG-alias checks.
- [C candidate contract probes](hash-c-candidate-contracts.json): 105 published
  reference-vector checks plus accepted-input and exception differences.
- [Experiment driver](../crypto_backends.py): candidates load independently;
  production dispatch and installed project dependencies are never patched.

Observed runtime: CPython 3.13.14, macOS arm64, 18 logical CPUs; `blake3` 1.0.10,
`pybase64` 1.5.0, stdlib OpenSSL 3.5.7. Development `cryptography` 50.0.2 uses a
separate OpenSSL 4.0.3. PyCryptodome 4.1.0 and an experimental C BLAKE3 build
were isolated outside the workspace environment.

## Function-by-function decisions

| Operation | Actual backend | Evaluation and decision |
| --- | --- | --- |
| `digest.raw` | Official Rust BLAKE3; size-dependent fresh Rayon pool | Keep algorithm/backend. Revisit pool economics on deployment CPUs; different hashes invalidate content keys. |
| `digest.hex` including truncation | Same BLAKE3, native digest then `bytes.hex()` | Keep. Width validation, custom index behavior, output and errors must survive any change. Short outputs reduce collision resistance. |
| `digest.derive_key` | Official BLAKE3 derive-key mode | Keep domain separation and context handling. A password KDF changes outputs and intentionally adds work. |
| `digest.legacy_blake2b` | CPython native `hashlib.blake2b` | Keep. PyCryptodome supports required widths but loses badly on small calls; PyCA supports only width 64. Digest-size parameterization cannot be replaced by truncation. |
| `digest.Stream.__init__` | Serial BLAKE3; optional bounded C batching | Keep. Existing batching benefits tiny materialized chunks; generic iterators must remain eager and observable. |
| `digest.Stream.update` | Native BLAKE3 update | Keep. New buffering changes when errors and callbacks occur. |
| `digest.Stream.raw` | Native nondestructive BLAKE3 finalization | Keep. Destructive alternatives require copying or alter reuse. |
| `digest.Stream.hex` including truncation | Native finalization plus `bytes.hex()` | Keep the same validated truncation path and reusable state. |
| `token.equal` | Guards plus stdlib `hmac.compare_digest`, locally OpenSSL `CRYPTO_memcmp` | Keep. No new FFI layer is justified; retain complete fixed-width tag comparisons. |
| `token.mac_raw` | Official keyed BLAKE3 | Keep 32-byte keys and tags. HMAC-SHA-256, keyed BLAKE2, and libsodium produce different tags. |
| `token.mac_hex` | Same keyed BLAKE3 plus `bytes.hex()` | Keep. Do not pay for a second crypto stack to format a tag. |
| `token.verify_mac` | Keyed BLAKE3 plus constant-time comparison | Keep raw/text behavior and key errors; parser shortcuts are separate experiments. |
| `token.mint` | Keyed BLAKE3 plus optional SIMD Base64 | Keep exact encoded-payload MAC and envelope format. Stdlib codec replacement has no general speed advantage. |
| `token.verify` | Same MAC/comparison plus canonical Base64 validation | Keep security checks. Bound request sizes at the caller; a parser optimization needs malformed-input and exception-order testing. |
| `tls.client_context` | CPython `ssl` / linked OpenSSL | Keep actual `ssl.SSLContext` type and verified TLS policy; patch runtime OpenSSL. Other engines require transport integration. |
| `rand.crypto.token_bytes` | Exact `secrets.token_bytes` alias / OS RNG | Keep. No unstd wrapper overhead exists to remove. |
| `rand.crypto.token_hex` | Exact `secrets.token_hex` alias | Keep entropy/error/default behavior; existing hex formatting is native. |
| `rand.crypto.token_urlsafe` | Exact `secrets.token_urlsafe` alias | Keep. SIMD formatting at large sizes is a separate experiment, not stronger randomness. |
| `rand.crypto.below` | Exact `secrets.randbelow` alias / rejection sampling | Keep arbitrary-width uniform draws. Modulo reduction is biased. |
| `rand.crypto.bits` | Exact `secrets.randbits` alias | Keep arbitrary widths, OS entropy, and error propagation. |
| `rand.crypto.choice` | Exact `secrets.choice` alias | Keep uniform selection and Python sequence semantics. |

Related operations were not overlooked: `uid.new` and `uid.new_hex` already use
`uuid-utils` when installed, with fork reseeding; fallback counter seeding and
the random tail use `os.urandom`. `uid.derive` is the exact stdlib UUIDv5/SHA-1
operation for persisted identities, not a security hash. Replacing its digest
changes every derived UUID. `uid.parse` only parses. `fs._scratch` uses stdlib
`secrets.token_hex(4)` plus exclusive file creation; exclusive creation and
permissions enforce the filesystem boundary. None merits a new crypto backend.
[UUID security considerations](https://www.rfc-editor.org/rfc/rfc9562.html#section-8),
[stdlib secrets](https://docs.python.org/3/library/secrets.html).

## Security actions and their limits

1. **Patch the runtime TLS engine first.** The observed OpenSSL 3.5.7 is
   affected by CVE-2026-35189: crafted certificate CRL distribution points can
   cause excessive allocation during normal handshakes. OpenSSL lists 3.5.9
   as fixed. Updating the separate `cryptography` package does not update
   stdlib TLS. Validate the patched runtime on deployment hardware; this audit
   did not replace the interpreter. [Official advisory](https://openssl-library.org/news/secadv/20260929.txt).
2. **Consider a documented BLAKE3 minimum-version exception.** The current
   `>=1.0` policy admits releases before 1.0.10's concurrency fix and dependency
   removal. The installed/locked 1.0.10 already includes them. The reported
   deadlock needs a shared parallel hasher; ordinary unstd calls do not expose
   that pattern. 1.0.9 locked unaffected `arrayref` 0.3.9; only 0.3.10 was
   malicious. This is a dependency-maintenance recommendation, not a claim
   that older wheels were compromised. A metadata floor adds no per-call
   version check. [BLAKE3 Python release](https://github.com/oconnor663/blake3-py/releases/tag/1.0.10),
   [RustSec applicability](https://rustsec.org/advisories/RUSTSEC-2026-0260.html).
3. **Bound untrusted token sizes where requests enter the application.**
   Verification currently scans, decodes, and re-encodes attacker-controlled
   input before authenticating it. No MAC bypass was found, but unbounded input
   allows proportional CPU/memory work. A new hardcoded library limit changes
   accepted claims. Authenticating first also changes some invalid-key/error
   ordering; it needs an independently specified change.
4. **Preserve existing guarantees.** Retain canonical encoding, constant-time
   comparisons, high-entropy KDF material, OS entropy, fork/error behavior, and
   TLS trust/hostname verification. Key caches, global mutable TLS contexts,
   cached entropy, shorter tags, and weaker validation do not qualify as free
   security improvements. [BLAKE3 KDF requirements](https://docs.rs/blake3/latest/blake3/fn.derive_key.html),
   [comparison guarantees and limits](https://docs.python.org/3/library/hmac.html#hmac.compare_digest).

## Alternatives actually evaluated

| Candidate | Evidence | Result |
| --- | --- | --- |
| Serial Rust BLAKE3 | Same public wrappers with only pool threshold disabled; plain hash, hex, KDF, MAC modes, 64 B–64 MiB | Can avoid expensive pool startup under contention; also loses useful large-input parallelism. No universal threshold inferred from this host. |
| Experimental official C binding with fresh C 1.8.7 core | Built outside workspace; 105 reference checks pass; public-call timings and contract probes | Reject drop-in. Ignores `max_threads`, rejects NUL contexts and accepted `None`, changes buffer exceptions, and has deployment-target build warnings. Rust already uses ARM NEON kernels. |
| PyCryptodome 4.1.0 BLAKE2b | Widths 1/32/64, 64 B/1 KiB/1 MiB; exact digest checks | Reject default replacement. Small-call setup cost and another dependency; no consistent gain across workloads. |
| PyCA `Hash.hash(BLAKE2b(64), ...)` | One-shot path, prepared algorithm favors candidate; three input sizes | Reject general replacement. Widths 1–63 unsupported, plus additional dependency and setup. |
| Stdlib versus existing optional SIMD Base64 | Complete token mint/verify and isolated codecs, 42 B/1 KiB/64 KiB claims | Keep current optional acceleration. Neither provider wins every complete-envelope trial on this noisy host. |
| Rustls/AWS-LC | First-party x86_64 and ARM64 studies; Python integration requirements | Worth considering for a separately designed transport, not an `SSLContext` substitution. Native wins do not establish Python or ARM throughput. |
| pyOpenSSL, BoringSSL, s2n-tls | Primary API/project policies | No compatible, measured no-overhead replacement for this context factory. |
| libsodium/PyNaCl MAC/KDF/RNG | Primary algorithm, output-range, uniformity, and RNG documentation | Different hash/MAC/KDF contracts or redundant OS entropy access through extra bindings. |

The C prototype is useful evidence, not a maintained production backend.
Upstream recommends Rust bindings for most applications. Rustls' own ARM study
contains record-throughput losses, despite handshake gains. Those facts block
a blanket replacement based on language choice or advertised native speed.
[Official Python binding guidance](https://github.com/oconnor663/blake3-py),
[Rustls ARM measurements](https://rustls.dev/perf/2024-10-31-arm64/).

## Measurement interpretation

The JSON retains alternating current/candidate batch order and exact-value/type
checks before timing. Inputs are allocated beforehand. Ratios are **current
time / candidate time**, so values above one favor the candidate. Batch means
are not individual-call p95/p99 latency measurements. The separate function
baseline uses minimum batch costs and is not a portable performance floor.

This is a heavily loaded shared machine. Identical-code controls below the
parallel threshold vary materially, and the recorded noise flags must govern
interpretation. These experiments can reject unsuitable candidates and expose
tradeoffs; they cannot certify absent overhead or choose a production pool
threshold. The JSON includes source/extension hashes and the exact measured
driver source for reproduction.

All **91/91 comparisons were noisy** by the inherited 25% sample-spread rule.
The one-minute load average was **87.89 at start and 82.99 at finish**, versus
18 logical CPUs. Each comparison used 15 alternating rounds with a 5 ms
calibration target. Even a 64-byte serial control that executes the same public
wrapper and backend appeared 4.19x faster, demonstrating why headline ratios
cannot authorize a change here. The following observations are diagnostic,
not portable speed claims:

| Experiment | Median paired current/candidate time | Interpretation |
| --- | ---: | --- |
| 1 MiB raw hash, serial candidate | 8.71x | Fresh pools can be very expensive under contention. |
| 64 MiB raw hash, serial candidate | 0.73x | Removing parallelism can also lose; no blanket serial switch. |
| 64 B raw hash, experimental C | 1.86x | A possible small-call opportunity, disqualified by contract failures. |
| 64 MiB hex hash, experimental C | 0.09x | Experimental serial C is no blanket bulk improvement. |
| 64 B BLAKE2b-256, PyCryptodome | 0.13x | Additional setup overwhelms small legacy calls. |
| 1 MiB BLAKE2b-256, PyCryptodome | 0.99x | No convincing bulk gain to offset setup/dependency costs. |
| 64 KiB stream in 1 B chunks, Python loop | 0.07x | Existing native batching is valuable for this shape. |
| Token mint with 64 KiB claims, stdlib Base64 | 0.26x | Existing SIMD support remains useful for larger envelopes. |

Paired median ratios are calculated per round; they are not the ratio of the
two separately computed median costs. The artifact retains both estimators.

The earlier committed adaptive report already recorded 1/2 MiB raw-hash
regressions and small-stream dispatch overhead. Existing behavior therefore
also has overhead; retaining it is not a declaration that the entire current
stack is optimal. [Prior measurements](../crypto-hash-adaptive-results.md).

## Acceptance rule for a future swap

Require matching algorithms, persisted outputs, API/exception behavior, vectors,
negative security cases, and fork/mutable-buffer contracts first. Then use
quiet deployment-representative CPython 3.12–3.14 workers on Linux/macOS/Windows,
including x86_64 and ARM64. Compare cold import/setup, common tiny calls,
threshold neighbors, bulk buffers, iterator/list streams, malformed tokens,
parallel callers, peak RSS/allocations, CPU time, thread creation, throughput,
and latency distributions. TLS needs verified real-transport tests, equivalent
groups/ciphers/trust/session policy, and both transfer directions.

Reject a candidate with any confirmed relevant regression. An uncertain/noisy
result is insufficient for the user's zero-added-overhead requirement. Do not
add per-call probing, load sampling, key/context caches, or backend dispatch to
make a benchmark look faster. Import/build-time process CPU caps and explicitly
owned context reuse are credible targeted experiments; neither is certified by
these measurements. No researched backend presently clears this acceptance
rule, so no production replacement was made.

## Validation and reproduction

The existing focused suite passed **1,302 tests**, covering crypto, secure RNG,
UUIDs, filesystem boundaries, primitive selection, real TLS handshakes, and
the paired benchmark harness:

```console
.venv/bin/python -m pytest tests/suites/product/crypto tests/suites/product/rand tests/suites/product/ids tests/suites/product/fs tests/suites/assurance/security/structure tests/suites/assurance/security/pentest tests/suites/assurance/gates/bench/test_crypto_hash.py -q
```

The new driver passes Ruff formatting/lint and strict mypy. Install experiment
dependencies into an isolated directory, build the optional C candidate using
the exact tags/instructions in the hash report, then run:

```console
uv pip install --python .venv/bin/python --target /tmp/unstd-crypto-backend-candidates 'pycryptodome==4.1.0'
.venv/bin/python -m bench.crypto_backends --candidate-path /tmp/unstd-crypto-backend-candidates --blake3-c /absolute/path/to/experimental/blake3.so --rounds 15 --seconds 0.005 --output /tmp/crypto-backend-results.json
```

Omit `--blake3-c` to omit that experimental backend; a missing PyCryptodome
package is explicitly recorded. The driver additionally needs the crypto and
serde extras and development `cryptography>=47` for its one-shot experiment.
No alternate TLS engine, deployment-runtime upgrade, cross-platform performance
certification, full supply-chain audit, or sanitizer campaign was executed by
this investigation.
