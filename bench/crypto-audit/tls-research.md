# TLS backend evaluation — 2026-10-22

## Scope and observed runtime

The production TLS surface has one function, `unstd.crypto.tls.client_context`, and one type alias, `TlsContext = ssl.SSLContext`. The function validates three certificate paths and the protocol floor, calls `ssl.create_default_context(cafile=...)`, sets the floor, optionally loads the client certificate/key, and returns that actual stdlib context. There is no Python encryption, handshake, record-processing, socket, or stream implementation in this module. It imports with no optional dependency.

Observed in this checkout's `.venv`, without changing it:

| Component | Observed |
| --- | --- |
| Interpreter | CPython 3.13.14, macOS 26.6, arm64 |
| Production TLS engine | `ssl.OPENSSL_VERSION`: OpenSSL 3.5.7, 9 June 2026 |
| Test PKI dependency | `cryptography` 50.0.2 |
| Test PKI engine | `cryptography`'s backend: OpenSSL 4.0.3, 29 September 2026 |

The production and development OpenSSL copies are separate. Updating `cryptography` alone does not change the TLS engine used by `ssl`.

## Decision per function

| Public surface / operation | Recommendation | Reason and overhead boundary |
| --- | --- | --- |
| `client_context()` | Retain stdlib `ssl` and its actual `SSLContext` result. Update the interpreter/runtime's linked OpenSSL to a maintained, patched release. | Preserves socket and asyncio integration, existing exceptions, certificate behavior, base-install support, and direct native record processing. No dispatcher, FFI adapter, or new Python wrapper on the transport path. |
| `client_context(cafile=...)` | Retain explicit private trust-store loading. | The existing tests verify that a supplied root excludes otherwise trusted public/default roots. An alternate engine must reproduce this exact trust boundary and system-store behavior. |
| `client_context(certfile=..., keyfile=...)` and combined PEM | Retain native `load_cert_chain`. | Existing tests verify identity loading, mismatched keys, combined PEM, missing identity, and peer-enforced mTLS. An adapter adds parsing/ownership/error-conversion work without demonstrated gain. |
| `client_context(minimum_version=...)` | Retain TLS 1.3 default and explicit TLS 1.2 legacy exception. | These are protocol policy, not a pluggable acceleration layer. Do not weaken verification or TLS floors for performance. |
| `TlsContext` | Retain the alias. | Substituting another context type would break the declared public type and consumers expecting stdlib contexts. |

At application call sites, configure one context per trust/identity policy, then reuse it for multiple connections. CPython documents this as thread-safe provided the context is not reconfigured after use. Do not add a global library cache: contexts are mutable, PEMs and root stores rotate, and sharing a cached context can accidentally share security policy. Reuse is an ownership decision for the caller. The library has no transport call sites to optimize. [CPython 3.13 ssl documentation](https://docs.python.org/3.13/library/ssl.html#ssl.SSLContext)

## Concrete security maintenance finding

The observed OpenSSL 3.5.7 is affected by CVE-2026-35189, fixed in 3.5.9. A peer certificate with crafted CRL distribution-point extensions can trigger substantial memory allocation during an ordinary TLS handshake; enabling CRL checking is not required. Upgrade the runtime's linked library rather than add per-call version checks. Other September issues require DTLS, QUIC, SM2/generic-curve signing, or server-side context switching and are not demonstrated exposures in this client seam. P-256/P-384/P-521 are excluded from the generic-curve timing issue. These are upstream applicability statements; no hostile certificate exploit was executed. [OpenSSL 29 September 2026 advisory](https://openssl-library.org/news/secadv/20260929.txt)

A patched OpenSSL upgrade preserves the Python integration and can remove the malicious-certificate allocation path. Its normal-operation performance still needs validation on deployment hardware; this evaluation does not claim every upstream patch has identical timing.

Provisioning and release checks should inspect the actual `ssl.OPENSSL_VERSION_INFO`/`ssl.OPENSSL_VERSION` of each supported interpreter and compare against current upstream advisories for that release branch. This advisory's fixed versions include 3.5.9, 3.6.5, and 4.0.3: a simple global `>= 3.5.9` comparison would incorrectly accept vulnerable 3.6.0 or 4.0.0. Track newer advisories and distributor backports rather than treating this snapshot as a permanent allowlist. Keep the check in environment/release validation, outside the per-context and per-record paths.

## Backend alternatives researched

### Rustls + AWS-LC

Rustls is the strongest candidate for a future transport implementation where the application can choose a different transport API. It moves much of the TLS protocol state machine to memory-safe Rust; its cryptographic provider remains a separately chosen dependency, commonly AWS-LC. First-party C FFI bindings exist, but are not an `ssl.SSLContext` replacement. Their default configuration does not load the system trust anchors automatically. A Python integration would need socket/nonblocking I/O, certificate loading, error mapping, trust-store discovery, ownership, and packaging decisions. [Rustls FFI project](https://github.com/rustls/rustls-ffi), [Rustls provider documentation](https://docs.rs/rustls/0.23.34/rustls/#cryptography-providers)

The Rustls team's March 2026 benchmark compares Rustls 0.23.37 + AWS-LC-RS 1.16.0 with OpenSSL 3.6.1 on Debian/x86_64 Xeon E-2386G. TLS 1.3 AES-256-GCM send throughput was 7,421 versus 5,844 MB/s; receive was 7,333 versus 6,238 MB/s. These are native-library results, not Python measurements and not this ARM64 machine. [2026 x86_64 report and methodology](https://rustls.dev/perf/2026-03-07-report/)

The team's October 2024 ARM64 report is a counterexample to a blanket speedup claim. Rustls 0.23.15 versus OpenSSL 3.3.2 produced 2,173 versus 2,345 MB/s for TLS 1.2 AES-128-GCM receive, and 1,936 versus 1,953 MB/s for TLS 1.3 AES-256-GCM receive, despite faster handshakes. The tested Ampere Altra is also not Apple Silicon. Replacing this project's engine on the strength of headline handshake results would fail the requirement to avoid regressions. [2024 ARM64 report and methodology](https://rustls.dev/perf/2024-10-31-arm64/)

### PyCA cryptography / pyOpenSSL

`cryptography` supplies crypto primitives and X.509 tooling, not a compatible stdlib TLS context. PyCA describes its crypto operations as OpenSSL-based, with Rust used for non-crypto processing such as ASN.1. `pyOpenSSL` supplies a different, thin OpenSSL API; moving to it is not evidence of a faster underlying TLS engine, and would require consumer integration changes. Neither is accepted as a replacement here. [PyCA FAQ](https://cryptography.io/en/latest/faq/#why-does-cryptography-require-rust), [pyOpenSSL documentation](https://www.pyopenssl.org/en/stable/)

### BoringSSL

Google explicitly states that BoringSSL is not intended as a general-use dependency and offers no API/ABI stability guarantees. It can be appropriate inside an independently maintained native transport, but is an especially poor fit for replacing the engine behind a stdlib-faithful library context factory. [BoringSSL project policy](https://github.com/google/boringssl#boringssl)

### AWS s2n-tls

An independently maintained TLS protocol implementation with selectable libcrypto backends is useful in a transport that controls its native integration. It does not provide this module's stdlib context contract. Supporting a native engine and Python adapter would expand build, platform, trust-store, and error-handling responsibilities; no local evidence establishes an overall gain here. Its documented defensive delays on malformed handshakes also show why attack-path latency and normal throughput are separate measurements. [s2n-tls project and usage documentation](https://github.com/aws/s2n-tls)

## Hybrid post-quantum policy and overhead

OpenSSL 3.5 introduced hybrid TLS groups including X25519MLKEM768; its documentation describes comparable CPU cost to the matching classical ECDH group but much larger key-exchange messages. TLS 1.3 alone does not guarantee that a post-quantum group is negotiated. The result also depends on the linked OpenSSL version, build/providers, configuration, and peer. Keep any claim conditional. [OpenSSL 3.5 group documentation](https://docs.openssl.org/3.5/man3/SSL_CONF_cmd/)

The OpenSSL default offers a hybrid key share, which can increase ClientHello size enough to cross TCP segment boundaries. Its documentation records rare middlebox failures/timeouts and mitigation tradeoffs. Therefore a policy that simultaneously promises strictly unchanged wire overhead and stronger hybrid key exchange cannot be made universally. Do not force a group or introduce extra key shares as a speculative performance optimization. [OpenSSL key-share configuration documentation](https://docs.openssl.org/3.5/man3/SSL_CTX_set1_curves/)

## Evidence required before any future engine change

Use equivalent certificate chains, trust decisions, TLS versions, key-exchange groups, cipher suites, and session-reuse settings. Measure full and resumed handshakes, certificate/PEM setup separately, 64 B through multi-megabyte transfers, both directions, parallel clients, allocation/RSS, p50/p95/p99 latency, and cold import/setup. Keep checks and candidate selection outside timed hot operations; never benchmark with peer verification disabled. Run all existing negative handshake cases against the candidate, using the actual Python transport adapter and deployment hardware. Accept no replacement based only on a native crypto microbenchmark.

This investigation did not execute an alternate TLS engine or claim measured local TLS speedups. Existing root-agent benchmark runs were kept free of concurrent TLS timing activity. The practical immediate result is to retain the engine contract, fix the linked runtime's patch level, and avoid adding a speculative abstraction layer.

## Implemented follow-up — 2026-10-22

The project development interpreter has now moved within its existing minor
series from CPython 3.13.14 / OpenSSL 3.5.7 to **CPython 3.13.16 / OpenSSL 3.5.9**.
The package retains its Python 3.12 compatibility floor. This fixes the observed
upstream TLS runtime exposure while preserving the stdlib context contract.
Installed dependencies and native extensions were preserved during the
project-local interpreter migration; another package's bundled OpenSSL was not
mistaken for the engine used by `ssl`.

The new [stdlib-only preflight](../../tests/tooling/runtime/README.md) runs as
`python -m tests.tooling.runtime --json`, records the actual executable, Python
version, OpenSSL string/numeric fields and TLS 1.3 support, and exits unsuccessfully
for vulnerable/unreviewed engines. Its reviewed floors are 3.0.23, 3.4.8, 3.5.9,
3.6.5 and 4.0.3. Branch boundaries, prereleases, mismatched metadata, CLI failure
status and isolated execution without site packages have regression coverage.
Deployment distributors with backports need their own reviewed evidence; a
version string alone cannot establish the contents of such a patch.

The production TLS module gains no runtime dispatch, version guard or cache.
Its documentation now states that TLS 1.3 alone does not establish hybrid PQ
negotiation and that hybrid shares have additional wire size. It also describes
caller-owned context reuse, with real authenticated mTLS exchanges proving
that one configured context supports multiple connections. A separate policy
test proves repeated factory calls return independently mutable contexts. The
library has no transport consumers at which to replace repeated construction;
there is no safe library-owned context cache to add.

The runtime patch is a security fix, and context reuse is supported caller
behavior. These changes do not claim a measured deployment TLS speedup or
universally identical timing for an upstream security patch. Future transport
engine changes retain the verified-transport evidence requirements above.

## Measured caller-owned context reuse

The [paired runner](../crypto_tls_reuse.py) and
[complete evidence](tls-reuse-results.json) now measure the context reuse
opportunity on CPython 3.13.16 / OpenSSL 3.5.9, macOS arm64. Run from the root:

```console
python -m bench.crypto_tls_reuse --rounds 11 --repetitions 64
```

Each of 11 alternating pairs runs 64 complete, freshly authenticated TLS 1.3
handshakes per side using MemoryBIO. The baseline creates/configures a pinned
client context for each connection; the candidate uses one caller-owned
configured context. Both sides use the same server context, certificate chain,
private root, hostname, TLS 1.3 floor/ceiling, cipher and negotiated key-exchange
group. Session objects are never passed, server tickets are disabled, and every
handshake asserts that neither peer resumed a session. Separate wrong-hostname
and untrusted-root controls fail certificate verification; the mTLS profile also
checks the exact independently issued client certificate.

| Profile | Fresh context + handshake, median CPU batch mean | Reused context + handshake CPU | Paired median CPU speedup | CPU speedup after charging one context setup to each 64-call reused batch |
| --- | ---: | ---: | ---: | ---: |
| Pinned server authentication | 805 µs | 581 µs | 1.29× | 1.28× |
| Mutual TLS | 1128 µs | 774 µs | 1.45× | 1.44× |

Standalone factory/configuration plus context release cost 147 µs and 303 µs
CPU per call respectively. The separately measured one-time setups consumed
251 µs and 483 µs CPU. Preparing each fresh context outside the timed handshake
reduced the CPU comparison to approximately 1.04× / 1.05× respectively:
this experiment supports avoiding repeated configuration, with no substantial
demonstrated change to the underlying handshake implementation.

All 11 complete-call pairs favored reuse in CPU time. Wall timing was heavily
affected by scheduling: paired medians were 1.43× and 1.64×, but ranges spanned
0.19–5.34× and 0.76–5.58×. Those wall observations do not establish a latency
or no-regression claim. The raw samples retain every outlier.
These are local batch means, not individual latency percentiles or a portable
zero-regression certificate. PEM files were warm; the common PKI/server setup
and the final release of the long-lived reused context are outside per-call
timers. MemoryBIO excludes network latency and does not measure record
throughput or a wire-size optimization. Actual production transports should
measure the same ownership choice with their real policies and connection mix.

The JSON includes wall/CPU samples, setup accounting, security evidence, exact
runtime and source/interpreter hashes. There are still no application transport
consumers in this library at which to apply reuse automatically. The supported
win remains constructing one context in the caller's setup code, keeping its
policy unchanged during use, and rebuilding it on trust or identity rotation.
No global mutable context cache or production dispatch layer was added.
