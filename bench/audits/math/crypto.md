# Crypto, TLS, and compression arithmetic backend review

Reviewed 2026-10-22 America/Los_Angeles. This is a source and primary-source
review, reusing the existing measured evidence under `bench/crypto-audit/` and
`bench/gzip-native-reads.md`. No production source, dependency, or existing audit
artifact was changed. No new CPU benchmarks were run concurrently with the
parent investigation. Existing measurements are historical observations, not
results of this review.

## Decision

Retain the official Rust BLAKE3 binding, CPython BLAKE2b, stdlib constant-time
comparison, OS entropy, and patched stdlib/OpenSSL TLS. Their alternatives
either change mathematical outputs, fail an existing public contract, or lack
end-to-end evidence of lower cost. The current source already contains bounded
native token parsing and stream batching. Do not repeat those implementations
or represent them as newly introduced work.

The strongest new compression follow-ups are isolated qualification of
libdeflate 1.26 and a zlib-rs C-ABI inflater. Neither is an accepted swap yet.
There is no basis to promise zero additional overhead across every input and
platform. The existing evidence actually contains small-call regressions,
pool-creation costs, explicit setup costs, and compressed-reader fallbacks.

## Actual inspected runtime versus available source

A fresh lightweight dispatch probe used the workspace `.venv/bin/python`:

| Item | Observed |
| --- | --- |
| Python | CPython 3.13.16, Clang 22.1.3 |
| TLS | OpenSSL 3.5.9, 29 September 2026 |
| BLAKE3 Python distribution | 1.0.10 |
| BLAKE2b constructor | `_blake2.blake2b` |
| Constant-time primitive | `_hashlib.compare_digest` |
| Native crypto helper | Available; importing `digest` loads it |
| zlib-ng Python distribution | 1.0.0 |
| zlib-ng actual engine | `ZLIBNG_RUNTIME_VERSION == '2.2.5'` |
| stdlib zlib | `ZLIB_RUNTIME_VERSION == '1.2.12'` |
| Compression native helper | `unstd.compress._gzip_native` not importable |

Thus this specific environment currently uses zlib-ng for streamed read
inflation/CRC and stdlib for one-shot decompression. The checked-in native
inflater/libdeflate paths require a compiled extension; their presence in source
does not establish their execution. Other interpreters/wheels used by prior
compression measurements did execute them. `zlib_ng.ZLIB_RUNTIME_VERSION`
reports a compatibility value and must not be mistaken for the zlib-ng engine
version. The macOS platform `libz` version alone is insufficient to assess
Apple's downstream fixes; vendor patch provenance is required.

### Scope of the observed zlib 1.2.12 version

`zlib` is built into this interpreter, and `otool -L` of the resolved executable
confirms `/usr/lib/libz.1.dylib` (compatibility 1.0.0, current 1.2.12), rather
than a separately bundled upstream library. The host is Darwin 25.6.0 arm64.
The unmodified upstream version cannot be used as a vulnerability verdict for
that Apple-maintained binary.

For CVE-2022-37434 specifically, the upstream 1.2.13 fix concerns copying gzip
header extra-field bytes into a caller-provided `inflateGetHeader` buffer.
CPython's tagged 3.13.16 `zlibmodule.c` has no `inflateGetHeader` call, and the
inspected actual `gzip.decompress` parses its own header before constructing
`zlib.decompressobj(wbits=-zlib.MAX_WBITS)` for raw DEFLATE. The unstd native
inflater similarly calls `inflateInit2_`/`inflate`/`inflateEnd` without
`inflateGetHeader`. Thus this particular advisory is not a demonstrated
reachable flaw in the inspected gzip paths. Apple also explicitly lists
CVE-2022-37434 as fixed in macOS Ventura 13; a retained numeric string is not
proof of a missing downstream fix.
[Upstream fix](https://github.com/madler/zlib/commit/eff308af425b67093bab25f80f1ae950166bece1),
[1.2.13 release](https://github.com/madler/zlib/releases/tag/v1.2.13),
[Tagged CPython zlib source](https://github.com/python/cpython/blob/v3.13.16/Modules/zlibmodule.c),
[Tagged CPython gzip source](https://github.com/python/cpython/blob/v3.13.16/Lib/gzip.py),
[Apple's documented fix](https://support.apple.com/en-us/102853).

Likewise the MiniZip zip-header overflow fixes included in zlib 1.3.1 concern
`contrib/minizip`, not this project's gzip/raw-inflate entry points. The 2026
1.3.2 audit changes touch `inflateCopy`, `deflateCopy`, negative
`crc32_combine` lengths, legacy printf/utility code and optional table builds.
These public gzip operations do not call copy/combine/printf/untgz APIs.
CPython does expose `Decompress.copy()` via `inflateCopy`, but the inspected
gzip code does not invoke it. Do not extrapolate this narrow reachability
analysis to every consumer of the interpreter's zlib. The vendored non-macOS
inflater is already 1.3.2. Continue OS/runtime maintenance and build provenance
checks outside hot calls; this inspection found no specific demonstrated
exploitable zlib advisory in the active gzip paths.
[1.3.1 release](https://github.com/madler/zlib/releases/tag/v1.3.1),
[Versioned upstream changelog](https://github.com/madler/zlib/blob/v1.3.2/ChangeLog).

## Public function inventory

The function names below are exhaustive for `crypto/digest.py`,
`crypto/token.py`, `crypto/tls.py`, `rand/crypto.py`, and the five exported
`compress/gzipx.py` names. Constants and type aliases perform no mathematics.

| Public function | Mathematical/backend path | Decision and cost/security boundary |
| --- | --- | --- |
| `digest.raw(data)` | Official Rust BLAKE3-256. Exact bytes below 1 MiB and other accepted buffers use serial hashing; eligible larger bytes use the pool policy below. | Retain algorithm and provider. BLAKE2/SHA/xxHash are different identities. |
| `digest.hex(data, n=None)` | Same BLAKE3 construction, then `digest().hex()`; exact integer truncation uses BLAKE3 XOF prefix of 1–32 bytes. | Retain; narrow outputs reduce collision security. Preserve unusual width conversion and error behavior. |
| `digest.derive_key(context, material)` | BLAKE3 derive-key mode, context type check, same pool eligibility. | Retain high-entropy KDF. Argon2/scrypt intentionally consume more resources and change keys; password handling needs a separate API. |
| `digest.legacy_blake2b(data, width=...)` | `hashlib.blake2b(..., digest_size=width)`, observed CPython `_blake2` C provider. | Retain. Width parameter changes BLAKE2 initialization and is not equivalent to truncating a 64-byte hash. Persisted identities must remain stable. |
| `digest.Stream.__init__(chunks=())` | Serial BLAKE3 hasher; optional C batching for exact list/tuple with at least eight chunks. | Retain bounded path and eager generic iterators. No materializing generators or private PyO3 ABI access. |
| `digest.Stream.update(chunk)` | One backend BLAKE3 `update`. | Retain accepted buffer semantics and observation order. |
| `digest.Stream.raw()` | Non-destructive BLAKE3 finalization to 32 bytes. | Retain finalization without forced copying/reset. |
| `digest.Stream.hex(n=None)` | Non-destructive BLAKE3 finalization and Python byte-to-hex. | Retain current width conversion and XOF semantics. |
| `token.equal(a, b)` | Type/ASCII checks, `hmac.compare_digest`; observed OpenSSL-backed `_hashlib` function. | Retain. Ordinary `==` loses content-independent comparison. Type/length leakage is outside the primitive's content guarantee. |
| `token.mac_raw(key, data)` | Exact 32-byte key check, keyed BLAKE3 PRF, 32-byte output. | Retain full tag. HMAC-SHA256/libsodium BLAKE2b are wire-incompatible. |
| `token.mac_hex(key, data)` | Same keyed BLAKE3, raw digest then Python hex. | Retain. |
| `token.verify_mac(key, data, tag)` | Recompute raw/hex keyed BLAKE3 based on tag type, then `equal`. | Retain constant-time full comparison. |
| `token.mint(key, claims)` | Nonempty claims check; URL Base64 encoding, keyed BLAKE3 over encoded payload, URL Base64 tag. | Retain authenticated byte sequence and canonical token format. SIMD Base64 is a formatting dependency, not a stronger MAC. |
| `token.verify(key, token, max_token_chars=8192)` | Size bound before scanning; suffix/alphabet/length/terminal-bit validation; 32-byte tag decode; keyed BLAKE3 plus constant-time compare; claims decode only after authentication. Python path or already-loaded C parser selected at import. | Retain current guard and canonicality. It bounds work but cannot promise a free guard for already-cheap rejects. It does not implement expiry/audience/replay authorization. |
| `token.prepare_verifier(max_token_chars=8192)` | Setup validates policy, explicitly loads optional native parser, binds the same existing MAC/comparison/Base64; returned callable accepts key and token. | Retain explicit setup. No retained keys, tokens, hashers, or backend probes per request. Setup cost is real. |
| `tls.client_context(...)` | `ssl.create_default_context` → linked OpenSSL, optional CA/mTLS loading, TLS floor. | Retain stdlib context contract. Reuse configured caller-owned contexts; no mutable global cache. Actual key exchange/cipher algorithms are negotiated, not selected by this wrapper. |
| `rand.crypto.token_bytes(n=None)` | Exact `secrets.token_bytes` alias → OS CSPRNG. | Retain alias/default/error semantics. A pool/DRBG creates secret state and reseeding/fork obligations. |
| `rand.crypto.token_hex(n=None)` | Exact `secrets.token_hex` alias → entropy bytes → native Python hex. | Retain. |
| `rand.crypto.token_urlsafe(n=None)` | Exact `secrets.token_urlsafe` alias → entropy bytes → stdlib URL Base64. | Retain. Existing SIMD replacement experiments regressed defaults and lose exact alias identity. |
| `rand.crypto.below(n)` | Exact `secrets.randbelow` alias → unbiased rejection sampling. | Retain arbitrary-width bounds. `% n` creates bias; fixed-width native APIs are not equivalent. |
| `rand.crypto.bits(k)` | Exact `secrets.randbits` alias → OS entropy bytes → integer and bit trimming. | Retain arbitrary widths and error propagation. |
| `rand.crypto.choice(seq)` | Exact `secrets.choice` alias → uniform index → Python sequence access. | Retain sequence hooks/errors and cryptographic independence from seeded RNG. |
| `gzipx.compress(data, compresslevel, mtime)` | Optional python-zlib-ng compression, otherwise exact stdlib alias. Level/timestamp defaults mirror running interpreter. | Retain gzip output contract and levels 0–9. Ratio/output bytes may differ between compatible gzip encoders. |
| `gzipx.decompress(data)` | Optional native libdeflate whole-buffer decoder for eligible exact bytes; unsupported/rejected cases use stdlib. Currently helper absent, so stdlib fallback. | Retain validated and bounded speculative native path. Do not force libdeflate onto streaming or replay failures without accounting. |
| `gzipx.open(...)` | GzipFile creation plus optional TextIOWrapper. | Math delegated to per-file read/write backends. Preserve modes, seek and file ownership. |
| `gzipx.GzipFile` | Writes: python-zlib-ng. Reads: interpreter framing + optional native inflater/CRC; otherwise zlib-ng. Base install: exact stdlib class. | Keep this shape until any replacement passes bounded-read/error-order/concatenation/seek contracts. |
| `gzipx.GzipFile.__init__(...)` | Reading initializes interpreter framing/buffering over the chosen inflater; writing initializes python-zlib-ng. | Retain modes, ownership, filenames and native-to-stdlib exception translation. Constructor cost is separate from stream throughput. |
| `gzipx.GzipFile.write(data)` | Native zlib-ng compression through the base class, with exception-family translation. | Retain accepted buffers and bytes-written behavior. Whole-buffer-only libdeflate cannot serve this generic stream contract. |
| `gzipx.GzipFile.flush(zlib_mode)` | Base native compressor flush. | Retain existing flush modes; ISA-L's narrower mode set is not transparent. |
| `gzipx.GzipFile.close()` | Final compressor output/trailer and owned-file cleanup. | Retain finalization errors and caller-owned file lifetime. |
| Inherited `GzipFile.read` / `read1` / `readinto` / `peek` / iteration / `seek` | Interpreter buffering and `_Reader.read` over native or fallback inflation/CRC. | Qualify each consumption shape; seek may rewind/replay and cannot be replaced with an unseekable threaded stream. |
| `gzipx.BadGzipFile` | Exact stdlib exception class, no arithmetic. | Retain identity. |

### Relevant private mathematical primitives

`crypto._blake3._parallel_hasher` builds a fresh per-call 4-worker pool from
1–4 MiB or 8-worker pool from 4 MiB, capped at import by process CPU count
(host CPU count on Python 3.12). It retries serial hashing only for the
specific PyO3 ThreadPoolBuildError panic. Pool creation consumes CPU/memory;
single-call latency and service aggregate throughput can disagree. A process
global Rayon pool is a fork risk. Cached keyed hasher state would prolong key
lifetime; no safe public pool-only reuse was established by the existing audit.

`digest._hexdigest` keeps uncommon `__index__`/comparison behavior via backend
`hexdigest`. `token._keyed` validates exact key width and shares worker policy.

`crypto._stream_native.absorb` copies exact immutable chunks up to 2 KiB into a
bounded 64 KiB batch and calls a normal Python-bound `hasher.update`. Mutable
buffers/subclasses flush preceding bytes and pass through; arbitrary iterators
use Python. The helper owns no cryptographic implementation or secret cache.
Native `canonical_alphabet` checks ASCII sextets and unused terminal bits;
`verify_fast` delegates actual MAC/comparison/decoding. Its variable-time
classification concerns attacker-supplied public syntax, not a secret tag
comparison. It does not declare free-threaded extension support; adding a
declaration requires a separate shared-state/concurrency review.

`native/compress/src/adapter.c:checksum` calls `libdeflate_crc32`, using the gzip
IEEE polynomial, not CRC32C. Replacing it with the x86 SSE4.2 CRC32C instruction
or a cryptographic digest changes results. CRC protects against corruption,
not malicious forgery. `adapter.c:decompress` calls
`libdeflate_gzip_decompress_ex`, verifies full consumption/CRC/ISIZE, and bounds
untrusted ISIZE speculation by 64 MiB and a compressed-size expansion budget.
Small decoders are reused while holding the GIL; large calls own decoder state
and release it. Returning `None` causes stdlib fallback and may repeat work.
The bound is a speculative-allocation bound, not a total decompressed-output
limit on subsequent fallback.

`native/compress/src/inflate.c` wraps `inflateInit2_`, `inflate`, `inflateEnd`; on
macOS source links platform libz and elsewhere bundles prefixed zlib 1.3.2.
Allocation uses checked Python RAW-domain allocators. `stream.c` tracks bounded
input/output and native status; `compress._reader` retains interpreter gzip
framing, CRC/trailer/error order and chooses native inflater/CRC once at import.
Do not replace only the inflater and assume malformed-stream delivery is stable.

## Existing local evidence: useful wins and explicit overhead

The current `bench/crypto-audit/fixes.md` records these already implemented
changes and experiments:

- Native public token verification: 1.32–1.97× for authentic claims and
  1.82–2.93× for forgery/wrong-key claims, on its measured macOS arm64 host.
  Explicitly prepared setup measured 0.440 ms and 128 KiB peak RSS once;
  binary growth measured 2064 bytes. The direct Python size guard added 5–11 ns
  for three already-cheap malformed rejects. These facts prohibit describing
  every path as zero-overhead.
- Caller-owned TLS context reuse: authenticated TLS 1.3 MemoryBIO handshakes
  measured 1.29× server-auth CPU speedup / 1.45× mTLS, versus rebuilding
  configuration for each connection. Setup charged per 64-call batch retained
  1.28× / 1.44×. This is configuration avoidance, not a faster TLS engine or
  proof of unchanged network latency.
- Experimental C BLAKE3 with fresh 1.8.7 core passed 105 author-vector checks;
  its binding ignores worker counts and rejects embedded-NUL KDF contexts.
  It is therefore not a transparent replacement. Rust already selects native
  NEON/x86 kernels.
- RNG SIMD formatting lost about 4% CPU on defaults and 35% for empty tokens,
  despite larger-input wins; exact `secrets` alias semantics also changed.
- Earlier adaptive BLAKE3 evidence measured only 0.46× and 0.65× raw speedup at
  1 MiB / 2 MiB, and 20–45 ns empty/one-chunk dispatch costs. Most workloads in
  those earlier reports were flagged noisy. Thresholds require current quiet
  sequential and concurrent qualification, not inference from large buffers.

`bench/gzip-native-reads.md` also discloses regressions: four macOS median
rows increase CPU cost, including a 100-row buffer by 4.7% and bounded expansion
by 4.3%. Small/large two-pass uploads increased 0.2%/0.6%. Linux arm64 had a
small-upload regression too. These are existing candidate-wheel observations
against 3.1.3, not results for a new backend. They disprove blanket no-regression
claims about enabling the extra.

## Current primary-source research and candidates

### BLAKE3 and BLAKE2b: retain

The upstream Python project recommends its Rust binding for most applications;
the C binding is experimental. Its documentation specifically warns threading
can be slower below about 1 MB. The latest verified Python release 1.0.11
adds platform/wheel coverage; it does not advertise a hashing speedup.
[Binding docs](https://github.com/oconnor663/blake3-py),
[1.0.11 release](https://github.com/oconnor663/blake3-py/releases/tag/1.0.11).

The existing raised floor 1.0.10 is retained. The prior audit traces BLAKE3
1.8.7's removal of the compromised-publisher dependency `arrayref`; RustSec
identifies 0.3.10 as malicious and <=0.3.9 unaffected. That is source-build
supply-chain defense, not evidence that older locked 0.3.9 wheels were malware.
Maintain build-time provenance rather than add per-hash version checks.
[BLAKE3 1.8.7](https://github.com/BLAKE3-team/BLAKE3/releases/tag/1.8.7),
[RustSec advisory](https://rustsec.org/advisories/RUSTSEC-2026-0260.html).

The BLAKE3 KDF is explicitly unsuitable for passwords. HMAC-SHA256, libsodium's
keyed BLAKE2b, SHA-3 and xxHash produce different outputs/properties and cannot
silently replace any public current hash/MAC/KDF. CPython BLAKE2b is already
native; PyCryptodome/PyCA candidates in the existing evidence add small-call
cost or lack required variable widths.
[BLAKE3 derive_key](https://docs.rs/blake3/latest/blake3/fn.derive_key.html),
[Python hashlib](https://docs.python.org/3.13/library/hashlib.html),
[libsodium generic hash](https://doc.libsodium.org/hashing/generic_hashing).

### Constant-time comparison and entropy: retain

Python documents `compare_digest` as avoiding content-based early exit and
using OpenSSL `CRYPTO_memcmp` when available. Types and lengths may remain
observable. Moving it through another Python binding is no demonstrated
security or performance improvement. `secrets` uses the OS source; direct
getrandom is platform-specific and a user-space pool introduces state/fork/error
semantics rather than free entropy acceleration.
[Python hmac](https://docs.python.org/3.13/library/hmac.html),
[Python secrets](https://docs.python.org/3/library/secrets.html),
[Python OS randomness](https://docs.python.org/3/library/os.html#os.urandom).

### TLS: retain patched OpenSSL and caller reuse

The official current vulnerabilities index confirms the September 29 fixes,
including malicious-certificate CRL-distribution-point allocation pressure in
3.5 before 3.5.9. The inspected 3.5.9 satisfies that reviewed branch floor.
This is runtime patch provenance; a package's separate bundled OpenSSL is not
the engine used by Python `ssl`.
[OpenSSL vulnerabilities](https://openssl-library.org/news/vulnerabilities/),
[September advisory](https://openssl-library.org/news/secadv/20260929.txt).

Rustls/AWS-LC is a credible separately designed transport option, but not an
`ssl.SSLContext` replacement; trust roots, sockets, verification/errors and
packaging need an adapter. Published native x86 wins do not prove Apple Silicon
Python request wins. Earlier ARM results include slower record cases. PyCA or
pyOpenSSL often keep OpenSSL beneath another binding. BoringSSL does not promise
general API/ABI stability. None establishes the required compatible,
end-to-end overhead-free replacement.
[Rustls providers](https://docs.rs/rustls/latest/rustls/#cryptography-providers),
[Rustls C FFI](https://github.com/rustls/rustls-ffi),
[Rustls 2026 x86 measurements](https://rustls.dev/perf/2026-03-07-report/),
[Rustls ARM measurements](https://rustls.dev/perf/2024-10-31-arm64/),
[BoringSSL policy](https://github.com/google/boringssl#boringssl).

Python explicitly supports shared SSLContext use if not reconfigured after use.
Hybrid ML-KEM groups improve a separate threat property but enlarge handshake
key shares; TLS 1.3 alone does not establish negotiation of one. They cannot be
sold as universally unchanged wire overhead.
[Python ssl](https://docs.python.org/3.13/library/ssl.html#ssl.SSLContext),
[OpenSSL group/key-share docs](https://docs.openssl.org/3.5/man3/SSL_CTX_set1_curves/).

### libdeflate 1.26: qualify as the narrowest update

Vendored code is 1.25. Official 1.26, released August 22, adds Arm Neoverse
V-series CRC32 selection improvements and build fixes, and raises the compiler
floor to C11 (MSVC 2019 v16.8). No security fix is advertised for 1.26.
This is a plausible native dependency update for relevant server CPUs, not
evidence of an Apple Silicon speedup or stronger authentication.
[Official release](https://github.com/ebiggers/libdeflate/releases/tag/v1.26),
[Versioned release notes](https://github.com/ebiggers/libdeflate/blob/v1.26/NEWS.md).

libdeflate explicitly does not support streaming. Its CPU feature selection is
already native; forcing global `-march=native` removes wheel portability. A
1.26 experiment should retain the direct-build source manifest/license hashes,
check C11 compilation on every release target, and measure whole public reads
and CRC at tiny/normal/bulk sizes. Do not add a runtime backend loader solely to
access the version.
[Official README](https://github.com/ebiggers/libdeflate).

### zlib-rs 0.6.7: credible security/throughput experiment, not approved swap

The project provides a zlib-compatible C API and reports Rust memory-safety and
on-par zlib-ng throughput. Its project page records a completed ISRG audit and
adoption in Firefox; the 0.6.7 release is August 3, 2026. Linking its C ABI under
the existing narrow `inflate.c` boundary is a more relevant experiment than a
new Python compression wrapper. Measure native startup/state allocation, exact
bounded read work, malformed streams, CPU/RSS, and actual Python calls before
acceptance. Rust toolchain/build dependencies are genuine distribution costs.
[Project](https://trifectatech.org/projects/zlib-rs/),
[README and C API](https://github.com/trifectatechfoundation/zlib-rs),
[0.6.7 release](https://github.com/trifectatechfoundation/zlib-rs/releases/tag/v0.6.7).

Memory-safe language is not proof that resource exhaustion disappears: the
official GHSA-j3px-q95c-9683 documents a malicious-input stack-overflow denial
of service in <=0.3.1 fixed at 0.4.0. The current release is outside that range;
do not label it vulnerable from this historical advisory. Recent releases also
contain unsafe-code lifetime corrections. Any adoption still needs current
advisory/build-provenance and FFI/allocator review.
[Official advisory](https://github.com/trifectatechfoundation/zlib-rs/security/advisories/GHSA-j3px-q95c-9683),
[0.6.6 release](https://github.com/trifectatechfoundation/zlib-rs/releases/tag/v0.6.6).

### zlib-ng 2.3.3: distinguish wrapper and engine upgrades

Installed python-zlib-ng 1.0.0 bundles engine 2.2.5. The engine's official
current 2.3.3 has correctness/build fixes; 2.3.1 introduced Chorba CRC paths,
then 2.3.2 corrected a >256 KiB edge-case checksum bug. Newer engine does not
mean that a newer Python wrapper wheel bundles it. Building an isolated wrapper
against a reviewed engine is a candidate, not an authorized dependency edit.
Its expected CRC benefit mainly concerns CPUs lacking carryless multiplication;
this code's compiled read extension uses libdeflate CRC instead.
[Wrapper changelog](https://github.com/pycompression/python-zlib-ng/blob/develop/CHANGELOG.rst),
[Engine releases](https://github.com/zlib-ng/zlib-ng/releases),
[2.3.3 release](https://github.com/zlib-ng/zlib-ng/releases/tag/2.3.3).

The vendored non-macOS inflater is already zlib 1.3.2, whose upstream release
addresses an audit. Older platform version strings require vendor-specific
patch evidence. The audit fixes concern individual entry points; this review
does not infer that every listed zlib utility vulnerability is reachable from
raw `inflate`.
[zlib release and audit summary](https://zlib.net/).

### ISA-L and format changes: reject transparent replacement

ISA-L offers strong native compression kernels, but python-isal documents
levels 0–3, a different meaning for level 0, and limited flush/strategy choices.
Its threaded stream cannot seek. Those violate the existing gzip interface;
mapping levels or adding parallel paths would also add policy/setup work.
It remains an explicit different API candidate for an independently qualified
throughput-oriented caller, not a replacement for every existing function.
[python-isal contracts](https://github.com/pycompression/python-isal),
[ISA-L native interfaces](https://github.com/intel/isa-l).

Zstandard, LZ4 and Brotli change the gzip wire format. CRC32C changes gzip's
checksum polynomial. None is a backend-only swap. Hardware accelerators/GPU
paths require transfer, batching and deployment infrastructure; no repository
evidence supports them as overhead-free solutions for these mostly short calls.

## Acceptance for future changes

Accept exact values/types/signatures/exception order, auth-negative tests and
published vectors first. Compare existing public functions, with backend
selection completed outside request paths. Use deployment CPU/runtime/compiler,
alternating paired runs, quiet host, untouched baseline control, separate cold
startup/setup and steady-state timing, allocation/RSS and package/build size.
Measure tiny/default and malformed inputs as well as bulk; include BLAKE3
threshold neighbors and concurrent callers, secure RNG error/fork behavior,
gzip bounded reads/concatenations/seek/error delivery, and verified TLS with
equal trust/ciphers/groups/session reuse. A lower average does not establish
unchanged p95/p99, aggregate throughput, or every supported hardware target.

When no candidate passes the full contract and cost matrix, retaining the
current backend is the decision consistent with the requested no-overhead
constraint. Provisioning/advisory checks stay outside mathematical hot calls.
