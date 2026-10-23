# Digest and BLAKE3 backend research

Research date: October 22, 2026 (America/Los_Angeles). Scope: the digest module,
shared BLAKE3 construction, native stream batching, and same-algorithm backend
alternatives. Production code was not changed for this investigation. MAC/token
policy is covered by the separate token audit.

## Recommendation

Keep the official Rust-backed `blake3` package and CPython's native BLAKE2b as
the production backends. The strongest measured optimization already present
is C coalescing of small, materialized stream chunks. The strongest next
candidate is reducing avoidable wrapper/pool work, with separate acceptance
criteria for small messages, large messages, streams, and concurrent callers.
There is no source-based reason to expect a blanket Rust-to-C replacement to
improve both throughput and security without regressions.

A `blake3>=1.0.10` minimum is a reasonable defense-in-depth dependency policy
candidate: it includes an upstream concurrency fix and removes a dependency
whose publisher was compromised. This is not evidence that the current API or
installed 1.0.10 wheel is vulnerable. 1.0.11 is the latest release verified and
adds platform/CI coverage; no hash-speed or security improvement is claimed by
its release notes. [1.0.10 release](https://github.com/oconnor663/blake3-py/releases/tag/1.0.10),
[1.0.11 release](https://github.com/oconnor663/blake3-py/releases/tag/1.0.11).

## Existing dispatch and function decisions

| Function/path | Current backend/work | Decision and relevant cost |
| --- | --- | --- |
| `digest.raw(data)` | `blake3(data).digest()` below 1 MiB and for non-exact `bytes`; fresh 4-worker pool at 1–4 MiB, 8 workers thereafter, capped by CPU count | Keep primitive. Measure serial versus pooled calls across boundary and contention; pool creation is an explicit per-call cost. |
| `digest.hex(data)` | Same construction; 32-byte `.digest().hex()` | Keep current finalization unless a same-signature candidate wins tiny, normal, and large cases. Prior repository evidence already supports Python `bytes.hex()` versus backend `hexdigest()`. |
| `digest.hex(data,n)` / `Stream.hex(n)` | Validate `1<=n<=32`; direct integer widths use `.digest(length=n).hex()`, unusual index objects defer to backend `hexdigest` | Preserve conversion/errors and validation order. A faster algorithm or slicing BLAKE2 output is not equivalent. |
| `digest.derive_key(context,material)` | Type guard, official BLAKE3 derive-key mode, same size-dependent pooling | Keep domain-separated mode. Do not cache contexts or hashers to hide initialization cost. Embedded NUL and Unicode contexts must retain current behavior. |
| `digest.legacy_blake2b(data,width=...)` | CPython native `hashlib.blake2b(...,digest_size=width).digest()` | Keep. Width 1–64 is part of algorithm parameterization, not an output truncation. |
| `Stream.__init__` | New serial BLAKE3 hasher; optional C absorber for exact list/tuple of at least 8 chunks; otherwise eager Python loop | Preserve generic iterator/reentrancy behavior. Accelerating list/tuple tiny chunks does not justify copying or materializing generators. |
| `Stream.update` | Direct backend `.update()` | Already one native update per call. Caller batching is useful only when its eager-observation contract permits it. |
| `Stream.raw` / full `Stream.hex` | Non-destructive backend `.digest()` then optional Python `bytes.hex()` | Keep reusable finalization; destructive APIs would need copying and additional work. |
| `_parallel_hasher` | Construct pool per eligible call; retry serial only for the specific PyO3 pool-build panic | Keep narrow failure filtering and fork tests. Avoid a global/reused Rayon pool as an unmeasured shortcut. |

The same shared pool policy also affects `token.mac_raw`, `token.mac_hex`,
`verify_mac` through MAC construction, and token signing/verification when
large enough. Tiny tokens generally remain serial. Exact buffers other than
exact `bytes` intentionally do not take the parallel eligibility path, avoiding
new Python-level buffer inspection/copies and preserving accepted inputs.

## What existing evidence establishes about overhead

The repository's prior reports are unusually candid and must remain part of
any decision:

- `bench/crypto-hash-adaptive-results.md`: 1 MiB and 2 MiB raw hashes measured
  only 0.46x and 0.65x paired median speedup after lowering the pool threshold;
  empty and one-chunk stream dispatch added about 20–45 ns on that host.
- The same report records 14.50x for 1-byte stream chunks, 3.29x for 64-byte
  chunks, and 2.33x for 1 KiB chunks; 4 KiB chunks were approximately unchanged.
- 54/58 adaptive workloads, 40/41 tuning workloads, and 21/22 initial workloads
  were flagged noisy. Earlier runs had one-minute load averages around 40–67.
- `bench/crypto-hash-results.md` records an earlier small raw-hash regression
  from 377 ns to 402 ns due to the eligibility branch.

Those observations are evidence of existing tradeoffs, not a universal reason
to disable parallelism. They do rule out describing the current threshold or
a future replacement as unconditionally free of overhead. The current research
run also observed load averages above available logical CPUs. Parent audit
benchmarks record current observations separately.

`bench/crypto_hash.py` already checks source hashes, signatures, exports,
constants, result bytes/types, alternating order, and complete per-batch samples.
A replacement experiment must also measure independent concurrent callers,
not just latency of sequential calls. Fresh pools can improve a single 64 MiB
hash while lowering aggregate throughput when every request starts more workers.

## Versions and security applicability

Verified release history and source:

| Python package | Rust BLAKE3 in upstream Cargo.lock | PyO3 | `arrayref` |
| --- | --- | --- | --- |
| 1.0.9 | 1.8.5 | 0.29.0 | 0.3.9 |
| 1.0.10 | 1.8.7 | 0.29.2 | Removed |
| 1.0.11 | Same hashing implementation lineage as 1.0.10 | Release change concerns platforms/CI | Removed |

The installed backend and `uv.lock` at inspection were 1.0.10; the optional
extra still allowed 1.0.0 via `blake3>=1.0`. Upstream source lockfiles are the
basis for the table, not reverse-engineering the installed binary:
[1.0.9 Cargo.lock](https://github.com/oconnor663/blake3-py/blob/1.0.9/Cargo.lock),
[1.0.10 Cargo.lock](https://github.com/oconnor663/blake3-py/blob/1.0.10/Cargo.lock).
An application lock and a Python package requirement do not themselves audit
all Cargo versions used by an arbitrary future source rebuild.

Upstream issue #120 reports a deadlock when two Python threads update the
same explicitly parallel hasher. The fix moves the state lock outside the
Rayon-installed update closure. Supported unstd one-shot operations construct
private hashers, and `Stream` uses serial hashers. Python input/key/context
conversion occurs before constructor return, so ordinary conversion callbacks
do not publish that new private hasher. Consequently the reported shared-pool
update deadlock is not demonstrated through supported unstd calls. The tagged
source still has a pool-internal lock in `update_mmap`; do not claim the update
fix covers every direct upstream method. unstd does not expose `update_mmap`.
[Issue #120](https://github.com/oconnor663/blake3-py/issues/120),
[fix diff](https://github.com/oconnor663/blake3-py/pull/121/files),
[tagged implementation](https://github.com/oconnor663/blake3-py/blob/1.0.10/src/lib.rs).

BLAKE3 1.8.7 removed `arrayref` following compromise of its publisher. RustSec
identifies `arrayref=0.3.10` as the malicious version and `<=0.3.9` as unaffected;
1.0.9's committed lock used unaffected 0.3.9. This supports reducing dependency
exposure and reviewing source-build resolutions, not declaring older BLAKE3
wheels malicious. [BLAKE3 1.8.7 release](https://github.com/BLAKE3-team/BLAKE3/releases/tag/1.8.7),
[RUSTSEC-2026-0260](https://rustsec.org/advisories/RUSTSEC-2026-0260.html),
[Rust Security Response Team account](https://blog.rust-lang.org/2026/08/20/supply-chain-attack-on-arrayref/).

## Official C alternative: built, tested, rejected as a drop-in

Two isolated builds succeeded on CPython 3.13.14/macOS arm64:

1. Unmodified `blake3-py` tag 1.0.11 `c_impl` bindings. Its vendor header still
   reports BLAKE3 **1.3.3**.
2. Those same bindings with `vendor/` replaced by the official BLAKE3 **1.8.7**
   `c/` directory. This removes the stale-core confound for measurements.

Both are experimental upstream bindings. The upstream README recommends the
Rust package for most applications and does not plan to publish these bindings
on PyPI. [Python binding guidance](https://github.com/oconnor663/blake3-py/blob/1.0.11/README.md),
[experimental C guidance](https://github.com/oconnor663/blake3-py/blob/1.0.11/c_impl/README.md),
[vendored version](https://github.com/oconnor663/blake3-py/blob/1.0.11/c_impl/vendor/blake3.h).

All **105 published-vector checks** passed for the fresh-core candidate:
35 input lengths, three modes, 131 output bytes each. Contract probes are saved
in `hash-c-candidate-contracts.json`. Passing known answers is necessary but not
sufficient:

| Behavior | Current Rust binding | Experimental C binding |
| --- | --- | --- |
| `max_threads=4/8` | Explicit Rayon pool | Accepted but ignored; serial |
| `data=None` | Empty input | `TypeError` |
| NUL inside derive context | Accepted and hashed completely | `ValueError: embedded null character` |
| u32 buffer | `BufferError` | `ValueError` |
| Strided buffer refusal | `buffer is not contiguous` | Different `BufferError` message |
| Contiguous byte/signed-byte/char/2D buffers | Accepted | Tested accepted with same digest |
| Regular non-ASCII context | Accepted | Tested accepted with same digest |
| macOS deployment target | Existing distributed wheel | Upstream build warns static intrinsics archive was compiled for macOS 26 while extension advertises macOS 11 |

The ignored-worker and parsing behavior are visible in the
[C constructor source](https://github.com/oconnor663/blake3-py/blob/1.0.11/c_impl/blake3module.c).
The C core provides `blake3_hasher_init_derive_key_raw`, so fixing the NUL issue
is feasible; a maintained compatible wrapper and its validation costs would
still be required. Do not silently use a C-string API that truncates the
context at its first NUL.

The official current C core offers optional oneTBB multithreading, but the
experimental Python binding does not use it. Enabling it adds a C++20/oneTBB
build/runtime surface and requires fresh fork, thread-refusal, resource, and
throughput checks. [Official C documentation](https://github.com/BLAKE3-team/BLAKE3/blob/1.8.7/c/README.md).
The existing Rust build already compiles the C NEON kernel on little-endian
AArch64 and uses optimized x86 assembly where supported. C is therefore not a
new SIMD capability by itself. [Rust build logic](https://github.com/BLAKE3-team/BLAKE3/blob/1.8.7/build.rs).

Exact fetched tags:

- Python bindings 1.0.11: `8f6eccc03a938215df541174f3e8cdc374ee3a29`.
- Python bindings 1.0.10: `580947e2caf04b97af8f4f28962b33a83e6606f4`.
- C/Rust BLAKE3 1.8.7: `f3149ec5bb5449af877ba20377a11008ff499fa2`.

Build command, from the isolated `c_impl` working directory:

```console
/path/to/unstd/.venv/bin/python setup.py build_ext --inplace
```

The fresh-core build uses an otherwise identical temporary copy with only its
`vendor/` directory replaced by `BLAKE3-1.8.7/c/`. Neither was installed into the
workspace. Loading with `importlib.util.spec_from_file_location('blake3', path)`
and executing the resulting module without assigning `sys.modules['blake3']`
allows side-by-side comparison with an already imported Rust class. Temporary
binary paths are diagnostic conveniences; source tags and build instructions
are the reproducibility contract.

## Stream helper ABI, memory, and security

`_stream_native.c` does **not** use a capsule, inspect Rust object layouts, or
call private cryptographic symbols. It owns a normal bound Python `update`
reference and calls `PyObject_CallOneArg`. This avoids a new unstable Rust/PyO3
ABI dependency. Exact immutable byte chunks up to 2 KiB coalesce into at most
64 KiB, then copy to a temporary Python `bytes` for the backend call. It is
bounded-memory acceleration, not zero-copy acceleration.

The helper uses actual list/tuple iterators and owned chunk references; mutable
buffers and subclasses cause a flush and pass through unchanged. It releases
temporary storage on errors and checks signals at backend updates or every
4,096 chunks. Tests already cover known answers, finalizers, list mutation,
callbacks, exceptions, bounded copies, signals, and forced Python fallback.
There is no evidence that replacing this with direct access to PyO3 memory
would be a safe performance improvement.

The extension currently does not declare GIL-free support. In a source-built
free-threaded CPython environment, importing an undeclared extension can enable
the GIL. The published wheel matrix does not currently target `t` builds, so
this is a future compatibility/performance qualification, not a claim about
current release wheels. Do not add the declaration without concurrency review.
[CPython free-threading extension rules](https://docs.python.org/3.13/howto/free-threading-extensions.html).

## BLAKE2b alternative screen

CPython's `_blake2` is already native; its build can use libb2 or its included
optimized/reference code. The 3.13 code selects optimized x86 code only when
the relevant compiler SIMD macros are available. This leaves build-specific
headroom, especially on ARM, but does not establish a faster Python substitute.
[CPython native implementation](https://github.com/python/cpython/blob/v3.13.14/Modules/_blake2/blake2b_impl.c).

| Candidate | Compatibility and overhead assessment |
| --- | --- |
| `cryptography` BLAKE2b | Public Python API only supports 64-byte output; fails widths 1–63. Reject blanket replacement. [Docs](https://cryptography.io/en/latest/hazmat/primitives/cryptographic-hashes/#cryptography.hazmat.primitives.hashes.BLAKE2b). |
| libsodium/PyNaCl generic hash | BLAKE2b with advertised output range 16–64; does not match 1–64 contract. New binary/FFI dependency, no demonstrated win. [API constants](https://github.com/jedisct1/libsodium/blob/1.0.20/src/libsodium/include/sodium/crypto_generichash_blake2b.h). |
| PyCryptodome | Supports widths 1–64, so merits a future same-algorithm benchmark if large legacy hashes matter. New dependency and Python/FFI setup must beat native stdlib across supported sizes, buffers, and error cases. No adoption justified from documentation alone. [Docs](https://www.pycryptodome.org/src/hash/blake2b). |
| Direct OpenSSL EVP | Variable BLAKE2b output is supported from OpenSSL 3.2. Python's normal OpenSSL hash constructor does not expose the EVP `size` context control. A custom binding adds provider/context/FFI work; no no-overhead win established. [OpenSSL EVP BLAKE2 docs](https://docs.openssl.org/3.5/man7/EVP_MD-BLAKE2/). |
| BLAKE2bp, BLAKE3, SHA, xxHash | Different algorithms/output contracts. Excluded irrespective of throughput. |

## Concrete candidate experiments and acceptance

1. Compare current fresh pools with serial and smaller pools at 1/2/4/8/16/64
   MiB, including four or more independent callers, process CPU time, thread
   starts, peak native allocations, and p50/p95 latency. Retain fork/refusal
   safety. Do not auto-tune at runtime based on load: polling itself adds work.
2. Compare the built C candidates to serial Rust separately from wrapper changes.
   Current contract failures disqualify deployment even if tiny inputs win.
3. Try import-time process-affinity-aware CPU caps on Python 3.13+ using
   `os.process_cpu_count`, with 3.12 fallback. This adds no per-hash dispatch or
   syscall and can reduce oversubscription where affinity narrows usable CPUs;
   it does not guarantee cgroup quota detection. [Python OS docs](https://docs.python.org/3.13/library/os.html#os.process_cpu_count).
4. Profile existing stream buffering before changing buffer strategy. Avoid
   memoryview creation, caller-buffer aliases, all-input joins, new keys/context
   caches, or per-call capability checks without full measured justification.
5. Package-level version/build decisions can avoid adding work to successful
   crypto calls. An installed-backend version check on every call cannot.

The acceptance bar is unchanged algorithm and public behavior, independent
known answers, adversarial/buffer/fork checks, and no statistically supported
regression in any measured deployment-relevant class. A noisy macOS result
cannot certify all platforms; unsupported candidates should remain experiments.
