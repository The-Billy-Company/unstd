# Crypto fixes and optimization results

Implementation follow-up to the [backend investigation](README.md), reviewed
22 October 2026. The existing Rust BLAKE3, stdlib constant-time comparison,
OpenSSL TLS, optional SIMD Base64 and exact `secrets` aliases remain the
cryptographic backends. No new runtime dependency or cryptographic algorithm
was introduced by this work.

**The absolute zero-overhead condition is not established.** The mandatory
Python size/configuration guard adds about 5–11 ns on three already-cheap
malformed-input rejections. Native acceleration avoids that measured hot-path
regression, with an explicit setup cost detailed below. No alternative backend
was adopted on the strength of an unproven universal performance claim.

## Four security actions implemented

1. **Patch the interpreter's TLS engine.** The workspace now uses CPython
   3.13.16 linked to OpenSSL 3.5.9, replacing CPython 3.13.14/OpenSSL 3.5.7.
   `.python-version` records the development pin; Python 3.12+ remains supported.
   The actual old engine fails the new preflight and the patched engine passes.
   Other machines and deployed services must provision and validate their own
   interpreter; updating this checkout cannot replace their TLS engine.
2. **Require the reviewed BLAKE3 patch.** The crypto extra now requires
   `blake3>=1.0.10`, covering upstream concurrency correctness and dependency
   removal. The installed and locked version was already 1.0.10. No version
   sampling occurs during hashing. This does not assert that 1.0.9's unaffected
   locked `arrayref` dependency was compromised.
3. **Bound token verification before expensive work.** The complete encoded
   envelope defaults to 8192 characters. Oversized values return `None` before
   scanning, slicing, decoding or hashing. A positive exact integer selects
   another policy; `None` explicitly opts into the previous unbounded behavior
   after the application imposes its own request bound. Minting larger claims
   remains possible. Canonical envelopes inside the policy retain key errors.
4. **Pin security and compatibility guarantees.** Regression checks cover
   canonical alphabet/length/unused bits, full 32-byte constant-time MAC
   comparison, encoded-payload authentication, subclass hooks and callback/error
   order, CPU affinity and fork behavior, independent TLS contexts and repeated
   use of a configured mTLS context. Key derivation documents its high-entropy
   input requirement. Entropy aliases, TLS hostname/trust policy and existing
   algorithms remain intact.

The [stdlib-only runtime preflight](../../tests/tooling/runtime/README.md) is
run in CI, release tooling, daily supply-chain validation and each wheel's test
interpreter. Branch-specific floors follow the
[OpenSSL September advisory](https://openssl-library.org/news/secadv/20260929.txt).
It rejects unreviewed engines/branches and prereleases. It is a reviewed
snapshot, requiring updates for new advisories and distributor backports;
there is no runtime version check in library calls or handshakes.

## Accepted performance changes

The Python verifier checks canonical encoded lengths and pad bits directly,
authenticates the encoded payload, then decodes claims. Forged inputs no longer
allocate decoded claims or re-encode payload and tag before MAC verification.
Exact strings use the fixed-width tag suffix to avoid a separator search.

An optional parser in the existing `_stream_native` extension performs bounded
ASCII classification and calls the existing Python MAC, comparison and Base64
functions. It contains no new crypto implementation. It retains only module
references, fallback and static names, with no request key, token, hasher or
worker pool between calls. Non-exact strings use the compatible Python path.
The unbounded opt-in checks Python signals during large scans.

For token services, make setup explicit:

```python
from unstd.crypto import token

verify_bearer = token.prepare_verifier(max_token_chars=8192)  # startup
claims = verify_bearer(key, bearer)  # each request supplies its own key
```

Token-only imports keep the direct Python `verify`. If `digest` already loaded
the helper, `token` binds that loaded helper once. Calling `prepare_verifier`
later leaves public `verify` unchanged. Compiler-free installations keep the
Python implementation. No request probes optional modules or caches secrets.

Process CPU availability caps workers once at import on Python 3.13+, with the
host CPU-count fallback on 3.12. This prevents affinity-limited services from
selecting workers using only the host's CPU count. Existing thresholds, fresh
per-call pools, serial fallback and fork behavior remain intact.

TLS context reuse is explicit and caller-owned: construct one context per
trust/client-identity policy, finish configuration, and reuse it until policy
or certificates rotate. Repeated mTLS handshakes test this pattern. No mutable
global context cache or extra connection wrapper was added.
Verified fresh TLS 1.3 handshakes using a reused context gave a CPU-time speedup
of **1.29x for server authentication and 1.45x for mTLS** against
constructing/configuring a context for every connection. Charging one setup to
each 64-call batch still gave 1.28x/1.44x. Wall timing was heavily affected by
scheduling; this is no record-throughput or network-latency claim. See the
[TLS measurement details](tls-research.md#measured-caller-owned-context-reuse)
and [raw samples](tls-reuse-results.json).

## Measured verification calls

Thirty verify cases cover valid tokens, forgery/wrong keys, and malformed
envelopes, plus three unchanged controls. Each uses 19 alternating paired
rounds. Ratios below are before/after process CPU time; above one favors the
new code. Inputs and policy setup are outside the request timers.

| Route | Authentic claims | Forged / wrong-key claims | Evidence |
| --- | ---: | ---: | --- |
| Native helper already loaded by `digest` | 1.32–1.97x | 1.82–2.93x | [Integrated public calls](token-fix-results.json) |
| Explicitly prepared verifier | 1.27–2.00x | 1.78–2.97x | [Prepared calls](token-prepared-results.json) |
| Direct Python fallback | 1.04–1.26x | 1.27–1.57x | [Python calls](token-python-results.json) |

The ranges include 64 KiB claims only with the explicit unbounded policy.
All 30 native verification cases improved in these trials, including the
cheapest rejects. Python rejects also improve for most malformed shapes, but
empty text, short text without a separator, and an appended extra separator
cost about 5–11 ns more. Six isolated Python variants were rejected because
none removed those regressions without weakening the bound or compatibility.
The [complete overhead experiments](python-bound-overhead-results.json)
retain the variants, raw samples and differential checks. This security cost
is deliberately disclosed rather than counted as a performance win.

## Overhead accounting

An early version forced the C helper to load during every token import. Cold
tests detected a paired median increase of about 4.5 ms wall/2.5 ms CPU, so that
design was rejected. Final token-only imports never load the helper, and 31
paired fresh-process trials detected no import wall/CPU/RSS regression.
Explicit `prepare_verifier` setup costs a measured median **0.440 ms and
128 KiB peak RSS once**. The optional arm64 binary grows by **2064 bytes**.
Those are real setup/size costs, excluded explicitly from prepared-call timing.
The [cold-start evidence](cold-start-results.json) retains raw samples and
the [rejected automatic-load experiment](cold-forced-load-results.json).

These measurements describe one shared macOS arm64 host. Batch confidence
intervals are exploratory, and do not establish independent-call tail latency
or a universal zero-overhead guarantee on other CPUs, runtimes or builds.

## Candidates investigated and rejected

| Candidate | Result |
| --- | --- |
| Replace Rust BLAKE3 with the experimental C binding | Reference outputs pass, but ignored thread options, context/input/error incompatibilities and build issues prevent a drop-in replacement. Rust already uses native CPU kernels. |
| Reuse hashers or their pools | Public `copy()` creates a new pool; `reset()` retains key/mode. No safe public pool-only reuse exists. Caching keyed state would retain secrets and alter fork/security properties. |
| Force serial or smaller worker pools | Some medium/concurrent cases improve CPU and wall time, but sequential large messages lose substantial throughput. Keep current policy. |
| Remove a copy from native stream batching | Focused trials slowed tiny streams about 5–11%; bulk savings were small and inconclusive. Original batching code and binary path restored. |
| First native parser using branch classification | Bulk envelopes slowed roughly 3–5%. Replaced by the immutable ASCII lookup-table implementation before integration. |
| Alternative BLAKE2b providers | PyCryptodome loses small-call efficiency and adds a dependency; PyCA lacks required widths. Keep `hashlib`. |
| Replace optional SIMD Base64 globally | No consistent whole-token gain. Preserve current optional codec selection. |
| Replace OpenSSL with rustls/BoringSSL/PyCA wrappers | No compatible `ssl.SSLContext` replacement or demonstrated end-to-end no-regression result. Patch and reuse the existing engine. |
| SIMD formatting for random URL tokens | Full generation gives about 1.15–1.24x CPU-time speedup at larger sizes, but default calls regress about 4% CPU and zero-byte calls about 35%. It also loses the exact `secrets` alias. Keep the current path. |
| Cached entropy, changed UUID hashes, shorter tags | No compatible security/performance improvement. Keep OS-backed entropy and persisted identity/wire semantics. |

Raw evidence: [worker trials](pool-fix-results.json),
[rejected stream-copy trials](stream-copy-results.json),
[native parser development](token-native-results.json),
[table parser prototype](token-native-table-results.json), and the original
[backend comparisons](backend-results.json).
The [RNG findings](rng-format-findings.md) and
[full generation/codec measurements](rng-format-results.json) show why codec
speed alone cannot justify a crypto API replacement.

## Reproduce and inspect

`bench.crypto_fixes` compares saved pre-change crypto source with current public
calls, checks exact result types/values, alternates timing order and records
wall/process/thread CPU samples, source hashes and runtime versions. The bulk
64 KiB-claims cases explicitly opt out of the default bound; rejecting a
previously accepted large token is never counted as a throughput gain.

```console
python -m tests.tooling.runtime --json
python -m bench.crypto_fixes --compare-source /path/to/before/src --output /tmp/auto.json
python -m bench.crypto_fixes --compare-source /path/to/before/src --token-path python --output /tmp/python.json
python -m bench.crypto_fixes --compare-source /path/to/before/src --token-path prepared --output /tmp/prepared.json
python -m bench.crypto_fixes --compare-source /path/to/before/src --mode pools --output /tmp/pools.json
```

Validation completed:

- **1924 passed, 6 skipped, 2 deselected** across crypto, RNG, UUIDs, filesystem,
  security, fuzz replay, benchmark gates, supply-chain and release checks. The
  six skips require the locally absent `cibuildwheel`; the two default-excluded
  timing-harness tests were then run explicitly and both passed.
- **1097 passed on each of Python 3.12.15 and 3.14.8**, with separately compiled
  native crypto helpers, covering crypto, primitive structure, real TLS peers
  and runtime preflight. Python 3.13.16 is the main workspace test interpreter.
- The native extension compiled with `-Wall -Wextra -Werror` on all three ABIs.
  **27162 Python/native differential cases**, dedicated callback/GC/key-release
  tests, and the signal-delivery negative control passed.
- Production and isolated **ASAN/UBSAN** campaigns each passed 20 seeds plus
  **10000 mutations**, exercising public and both prepared size policies.
  Sanitizer leak detection was disabled; ownership/GC checks were separate.
  The [native safety record](native-safety-results.json) states exact commands,
  overlapping test counts, environment correction and instrumentation limits.
- Ruff and formatting checks passed for changed crypto/tooling/benchmark files.
  Strict mypy passed for Python 3.12/3.13/3.14 on Linux/macOS/Windows targets.
  CI ownership, locked resolution and whitespace checks passed.
- The [runtime evidence](runtime-fix-results.json) records the old engine's
  rejection, the patched engine's acceptance and preservation of all 56 installed
  distribution versions during the local interpreter migration. This statement
  concerns this migration, not unrelated pre-existing manifest/lock changes.

Two measured API workloads were added to the frozen speed registry using its
existing CPython 3.13.14 environment, preserving all 224 prior case values and
ceilings. The frozen snapshot remains explicitly tied to that interpreter;
verification correctly requires a separate baseline on the patched runtime.
The new cases distinguish repeated preparation with a loaded helper from
requests using a verifier prepared once.
