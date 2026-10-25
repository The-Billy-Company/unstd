# Token, KDF, secure randomness, and UUID backend research

Reviewed 2026-10-22 (America/Los_Angeles). This is an evaluation; no production
code, dependencies, or configuration were changed for this part of the audit.
The inspected environment is macOS arm64, CPython 3.13.14, OpenSSL 3.5.7,
blake3 1.0.10, uuid-utils 1.0.0, and pybase64 1.5.0. Conclusions about a backend
being suitable do not assert that every deployment or input gets faster.

## Decision

Keep the existing official Rust BLAKE3 backend for keyed MACs and derivation;
keep the OpenSSL-backed stdlib comparison; keep all six exact `secrets` aliases;
keep OS randomness in the UUID fallback and atomic-file naming. These paths
already use appropriate native security primitives. No researched replacement
demonstrates both stronger security and universally absent overhead while
preserving the public contracts and every persisted output.

The most promising token optimization is reducing envelope parsing work, not
substituting a different cryptographic primitive. An authenticate-before-decode
rewrite needs careful exception-order analysis and separate adversarial and
performance validation. It is not an approved drop-in change in this report.

## Public function inventory

| Function | Current backend and work | Decision and constraint |
|---|---|---|
| `token.equal(a, b)` | ASCII/type guards then `hmac.compare_digest` | Keep. Local `compare_digest.__module__` is `_hashlib`; CPython uses OpenSSL `CRYPTO_memcmp` when available. Type and length remain observable, as documented upstream. The guards inspect public representation, not key bytes. |
| `token.mac_raw(key, data)` | 32-byte-key check; keyed official Rust BLAKE3; 32-byte digest | Keep. Existing bulk worker policy is shared with ordinary digest. Do not add HMAC around BLAKE3 or silently replace its tag algorithm. |
| `token.mac_hex(key, data)` | Same keyed BLAKE3, native byte digest plus `bytes.hex()` | Keep; any change to native hex output must beat the existing already-tuned implementation on public calls. |
| `token.verify_mac(key, data, tag)` | Recompute raw or hex MAC according to tag type; `equal` | Keep. Hex decoding to raw is extra validation/work and would change uppercase or malformed-tag behavior. |
| `token.mint(key, claims)` | URL-safe unpadded Base64; MAC over the exact encoded payload; Base64 tag | Keep wire format. The codec already has optional pybase64 acceleration. Empty claims are explicitly invalid. There is no JSON reserialization. |
| `token.verify(key, token)` | Envelope/ASCII/alphabet checks; decode both parts; canonical re-encoding; fixed tag width; recompute MAC; compare | Keep pending an independently validated parser optimization. Never remove canonicality checks or return unauthenticated decoded claims. |
| `digest.derive_key(context, material)` | Reject non-string context; official BLAKE3 derivation mode; shared bulk policy | Keep. Context must be globally unique and application-specific; material should already contain strong key entropy. This is not password hashing. |
| `rand.crypto.token_bytes(n)` | Exact alias of `secrets.token_bytes` → `SystemRandom.randbytes` → OS CSPRNG | Keep: there is zero unstd wrapper-call overhead. `None` and omitted size use stdlib's chosen default. |
| `rand.crypto.token_hex(n)` | Exact alias of `secrets.token_hex`; one entropy draw then native `bytes.hex()` | Keep. Direct `os.urandom(n).hex()` would only remove stdlib adapter calls, not improve the entropy source, and loses default/signature/alias semantics. |
| `rand.crypto.token_urlsafe(n)` | Exact alias of `secrets.token_urlsafe`; OS draw plus stdlib URL Base64 | Keep. An optional SIMD encoder is a measurable future formatting experiment, not a security backend upgrade; small 32-byte defaults can be dominated by call/dispatch cost. |
| `rand.crypto.below(n)` | Exact alias of `secrets.randbelow`; SystemRandom rejection sampling | Keep arbitrary-width uniform integers and validation. `% n` is biased. libsodium's bounded integer API is only 32-bit and differs for n < 2. |
| `rand.crypto.bits(k)` | Exact alias of `secrets.randbits`; OS bytes → int → trim extra bits | Keep arbitrary bit lengths and entropy failure propagation. Never replace with seedable NumPy/random state. |
| `rand.crypto.choice(seq)` | Exact alias of `secrets.choice`; uniform bounded draw then sequence access | Keep non-empty sequence behavior and unbiased selection. Native replacement would still need Python sequence protocol and exception semantics. |
| `uid.new()` | One selected UUIDv7 backend plus canonical string formatting | Keep existing uuid-utils acceleration. UUIDv7 is an identity, not a bearer secret. |
| `uid.new_hex()` | One selected UUIDv7 backend plus hex extraction | Keep. Its native backend is already substantially faster than the fallback in the existing repository benchmark; do not turn identity generation into the secure-token API. |
| `uid.derive(namespace, name)` | Exact stdlib `uuid.uuid5` alias (SHA-1 name UUID) | Keep output/UUID return type and cross-language persisted identities. A faster UUIDv5 implementation would still use SHA-1. A stronger digest requires an explicitly new identity scheme and migration. |
| `uid.parse` | Exact stdlib `uuid.UUID` alias | No cryptographic backend to swap. |

Internal entropy sites: `uid._seed_counter()` requests two fresh OS bytes,
`uid._stdlib_uuid7()` requests eight fresh OS bytes for its 62-bit random tail,
and `fs._scratch()` requests four OS bytes through `secrets.token_hex(4)` for an
eight-character suffix. The filesystem boundary relies on `O_CREAT | O_EXCL`,
retry on collision, close-on-exec, and safe permissions; random suffix secrecy
is not what prevents overwriting an existing file.

## Security findings and limits

1. **No confirmed MAC/CSPRNG bypass was found in these inspected functions.**
   MAC widths, key widths, canonical encodings, exact encoded-payload coverage,
   and the real constant-time comparison result are explicitly tested.
   `verify` authenticates opaque bytes and deliberately does not interpret
   expiration, audience, authorization claims, replay policy, or JSON. Consumers
   must enforce those policies after verification.
2. **`token.verify` has no input bound.** Its alphabet replacement/scans,
   decoding, and canonical re-encoding all allocate or traverse work proportional
   to untrusted input before the MAC is checked. This is a resource-exhaustion
   exposure when callers expose it to arbitrary request sizes; it is not a MAC
   forgery. An application HTTP/header/body size limit is the cleanest policy
   boundary. Hardcoding a library limit would reject previously valid claims.
3. **Authenticating before decoding is not automatically semantics-preserving.**
   For a well-shaped alphabet-only payload `a`, valid-length canonical tag, and
   an invalid signer key, today's decoder returns `None` before reaching key
   validation. A MAC-first rewrite raises a key error. Canonical but forged
   tokens with large claims could avoid claim decoding/re-encoding after a
   MAC-first rejection, but authentic tokens still need those checks. A tag-length
   precheck can reject pathological tags sooner, but even one added branch can
   cost something on the common successful path. Benchmark before acceptance.
4. **Use BLAKE3 derivation with key material, not passwords.** Its speed is the
   benefit for high-entropy material; it does not supply memory-hard guessing
   resistance. Switching this function to Argon2/scrypt silently changes keys
   and intentionally adds CPU/memory work. Such a requirement needs a separate
   explicit password KDF API, not a backend substitution.
5. **Avoid caching keyed hashers.** A cache retains keys and mutable hash state
   longer, introduces synchronization, and changes fork/lifetime properties.
   The existing BLAKE3 seam expressly avoids it. Zeroization cannot be promised
   for caller-owned immutable Python `bytes` by changing the hash library.
6. **UUIDs must not be security capabilities.** RFC 9562 explicitly forbids
   assuming UUIDs are hard to guess or using possession of a UUID as access
   authority. The fallback has a visible timestamp and counter; uuid-utils uses
   Rust's fast RNG feature. Its permitted 0.17.0 floor and inspected 1.0.0
   installed package both register a child-after-fork RNG reseed hook, so the
   obvious inherited-RNG issue is already addressed. The independent unstd
   fallback also replaces its lock and resets its counter in forked children.
7. **UUID fallback documentation needs precision.** CPython's UUIDv7 uses a
   42-bit counter and a 32-bit fresh tail, whereas unstd's handwritten fallback
   uses a 12-bit counter and a 62-bit fresh tail. Both use the same RFC counter
   method, but their random-bit/counter layouts are not identical. This is a
   documentation issue, not grounds to change either identifier format.

## Alternatives considered

| Alternative | Outcome |
|---|---|
| `hashlib`/OpenSSL HMAC-SHA-256 | Mature and often accelerated, but a different MAC algorithm/wire value; no compatibility or universal latency/security win over keyed BLAKE3. |
| libsodium/PyNaCl MAC/KDF | libsodium's normal keyed generic hash is BLAKE2b; it is not keyed BLAKE3. Different outputs plus another dependency/binding. Secure memory APIs cannot retroactively erase copies of input Python bytes. |
| Official BLAKE3 C bindings | Upstream calls its Python C bindings experimental and recommends production Rust bindings for most applications. Losing Rust's multithreading support is not an acceptable unmeasured swap. SIMD is already part of the selected official implementation. |
| libsodium `randombytes_buf` | Uses OS RNG facilities too. A Python FFI/allocation layer is not evidence of less overhead or more secure entropy than stdlib. Its default is fork-safe, while custom generators require explicit child reseeding. |
| User-space ChaCha/AES DRBG or cached random-byte pool | Can amortize OS calls, but adds secret state, memory retention, synchronization, reseeding/fork/VM restore obligations, and changes immediate OS error propagation. Not a zero-overhead security improvement. |
| `os.getrandom` directly | Linux-only, can return fewer bytes than requested, and requires looping/error policy. `os.urandom` already handles portable OS details and blocks for initialization on Linux. |
| Direct `os.urandom` alias for token bytes | Different omitted/None/default/keyword behavior; violates existing exact `secrets` identity tests. It is a lower-bound measurement, not a compatible replacement. |
| NumPy/default `random` or modulo sampling | Reject: changes the cryptographic or uniformity guarantee. |
| SIMD Base64 for random URL tokens | Potential formatting-only speedup at large sizes; default tokens are tiny, stdlib aliases are deliberate, and an extra dependency/dispatch path is not free. Existing signed-envelope codecs already use pybase64 where appropriate. |
| Replace UUIDv5 SHA-1 with SHA-256/BLAKE3 | Reject silent substitution: not UUIDv5 and renames stored cross-service identities. |

## Benchmark acceptance plan

Do not certify absent overhead from source inspection or a bulk hash benchmark.
For any proposed backend change, retain exact vectors and canonical token tests,
then compare **public calls** with input allocation outside timing. Include empty,
32, 256, 4 KiB, 64 KiB, 1 MiB, and worker-threshold-neighbor sizes for MAC/KDF;
realistic 32/256/2048-byte claims and large malformed payloads for envelopes;
RNG default/explicit/zero sizes and bounded draws around powers of two. Include
valid, wrong-key, wrong-tag, bad-padding, unused-bit alias, Unicode, extra-dot,
and malformed-key-order cases. Run seeded/fork/error tests separately.

Record warm latency distributions, throughput, allocations/RSS, cold import,
thread counts, and multi-process/fork behavior on supported CPython/platform
combinations. Interleave old/new order; use a quiet host. A win on bulk buffers
does not excuse regression on common tiny tokens, defaults, malformed input,
cold startup, memory, dependencies, or fork handling. Existing no-op aliases
are useful noise controls. No heavy concurrent timings were run by this agent
while the parent was measuring the main benchmark matrix.

## Primary sources

- [Official BLAKE3 implementation and supported modes](https://github.com/BLAKE3-team/BLAKE3)
- [Official Python Rust bindings, MAC/KDF examples, threads, and experimental C status](https://github.com/oconnor663/blake3-py)
- [Python binding implementation](https://raw.githubusercontent.com/oconnor663/blake3-py/master/src/lib.rs)
- [Official BLAKE3 KDF and explicit password prohibition](https://docs.rs/blake3/latest/blake3/fn.derive_key.html)
- [Python secrets API](https://docs.python.org/3/library/secrets.html)
- [CPython 3.13 secrets implementation](https://raw.githubusercontent.com/python/cpython/3.13/Lib/secrets.py)
- [CPython OS RNG implementation](https://raw.githubusercontent.com/python/cpython/3.13/Python/bootstrap_hash.c)
- [OS random APIs and initialization behavior](https://docs.python.org/3/library/os.html#os.urandom)
- [Constant-time comparison and documented type/length limits](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)
- [Libsodium randomness, uniformity, fork and VM caveats](https://doc.libsodium.org/generating_random_data)
- [Libsodium generic hashing is BLAKE2b](https://doc.libsodium.org/hashing/generic_hashing)
- [Libsodium key derivation is BLAKE2b with different context and key-id parameters](https://doc.libsodium.org/key_derivation)
- [RFC 9562, especially security considerations](https://www.rfc-editor.org/rfc/rfc9562.html#section-8)
- [uuid-utils 1.0.0 selected Rust RNG features](https://raw.githubusercontent.com/aminalaee/uuid-utils/1.0.0/Cargo.toml)
- [uuid-utils 0.17.0 floor fork reseed registration](https://raw.githubusercontent.com/aminalaee/uuid-utils/0.17.0/python/uuid_utils/__init__.py)
- [uuid-utils 1.0.0 fork reseed registration](https://raw.githubusercontent.com/aminalaee/uuid-utils/1.0.0/python/uuid_utils/__init__.py)
- [Rust ThreadRng security and fork obligations](https://docs.rs/rand/latest/rand/rngs/struct.ThreadRng.html)
- [UUID crate RNG backend selection](https://raw.githubusercontent.com/uuid-rs/uuid/main/src/rng.rs)
- [CPython UUID implementation](https://github.com/python/cpython/blob/main/Lib/uuid.py)
- [CPython UUIDv5 SHA-1 and parsing API](https://docs.python.org/3.13/library/uuid.html#uuid.uuid5)

Direct environment probes confirmed all six secure RNG exports are the exact
`secrets` function objects, the comparison provider is `_hashlib`, the selected
UUID backend is `uuid_utils._uuid_utils`, and the malformed-payload/invalid-key
ordering example returns `None`. These observations verify dispatch and
semantics; they make no cryptographic timing or global entropy-quality claim.

## Existing relevant validation

- `tests/suites/product/crypto/security/test_token_boundary.py`: independent
  envelopes, exact bytes, every byte flip, Unicode, malformed separators,
  canonicality including unused pad bits, wrong keys, deliberate expired claims.
- `tests/suites/assurance/security/structure/test_mac_primitives.py`: actually
  executes the constant-time primitive, compares full tags, and honors refusal.
- `tests/suites/assurance/security/structure/test_random_primitives.py`: exact
  stdlib primitive identity, seed immunity, live entropy, failure propagation.
- `tests/suites/product/crypto/regression/test_derive_key_context.py`: rejects
  None context so derivation cannot silently become public plain hashing.
- `tests/suites/product/ids/integration/test_process_boundaries.py` and UUID
  concurrency/fork suites: import-time backend selection and process behavior.
- `tests/suites/product/fs/integration/test_filesystem_boundaries.py`: random
  suffix collisions and exclusive temporary-file creation boundaries.
