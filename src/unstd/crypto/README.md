# `unstd.crypto`

One cryptographic seam: BLAKE3 digests, keyed-BLAKE3 MACs, and a verifying TLS
client policy. The point of a seam is that there is exactly one of it - no call
site reaches past this package into `hashlib`, and no call site invents its own
constant-time compare.

```python
from unstd.crypto import digest, token, tls

digest.hex(b"content")  # BLAKE3-256, 64 hex chars
digest.hex(b"content", 8)  # a 16-char short id
digest.derive_key("myapp session key v1", km)  # a domain-separated subkey
token.mint(key, claims_json_bytes)  # payload.tag bearer
token.verify(key, bearer)  # claims bytes, or None
tls.client_context(cafile="private-root.pem")
```

Install with the extra: `pip install 'unstd[crypto]'`.

## Modules

| File        | Role                                                                                                      |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| `digest.py` | BLAKE3-256 - `raw` (bytes) / `hex` (full or truncated) / `Stream`, `derive_key`, and the `legacy_blake2b` escape hatch. |
| `_stream_native.c` | Optional bounded stream batching and canonical token-envelope parsing; cryptographic primitives remain in their existing backends. |
| `token.py`  | Keyed-BLAKE3 `mac_raw` / `mac_hex` / `verify_mac`, constant-time `equal`, and the claims envelope.         |
| `tls.py`    | A verifying TLS client context with no parameter for skipping verification.                                |

There is deliberately no sealing or key-management module. An empty one would be
a promise nobody kept.

## Why `derive_key` sits in `digest`

BLAKE3 key derivation is a mode of the same compression function - same
permutation, different initial flags - not a separate key-management concern, and
splitting it out would imply a keystore that does not exist.

What makes it safe is the *context*. Register one versioned string per purpose,
somewhere your program can be read: two unrelated uses then cannot derive the
same subkey out of the same material, and a context differing by one character
gives a wholly unrelated key. A context that is not a `str`, including `None`,
raises `TypeError` rather than falling back to the plain digest.

## The token format

```text
base64url_nopad(claims_json) "." base64url_nopad(keyed_mac(base64url claims bytes))
```

The MAC covers the *encoded* payload rather than the raw JSON, so a verifier
never re-serializes to check a tag. That also means claim key order is part of
what is signed, which is why `mint` takes bytes you already serialized instead of
an object.

`verify` fails closed on every shape problem - a missing or doubled separator, a
non-base64 segment, a tag of the wrong width - and returns `None` rather than
raising, because the input is attacker-controlled and a 500 is a signal.

Verification defaults to an **8192-character limit for the complete token**,
including its separator and tag. Oversized inputs return `None` before the
verifier slices, scans, decodes, or hashes their contents. Applications can
choose a smaller positive `max_token_chars` that matches their request policy.
Limits must be exact integers; booleans, integer subclasses, and other types
raise `TypeError`. Zero and negative limits raise `ValueError`. These are
configuration errors, as are invalid
signer keys on well-shaped tokens within the limit.

This limit is a deliberate security tightening: previously accepted larger
tokens need an explicit larger limit. `mint` still accepts arbitrary nonempty
claims. For bulk envelopes already bounded at the application boundary, use
`token.verify(key, bearer, max_token_chars=None)` to retain unbounded behavior.
The wire format, exact claim bytes, and full 32-byte MAC stay the same.

The verifier checks the URL alphabet, encoded lengths, and unused terminal
bits directly, then authenticates the encoded payload before decoding claims.
This preserves canonical spelling while avoiding claim decoding and
re-encoding for forged tokens. Expiry, audience, replay, and authorization
remain application policies applied after verification.

For a service that verifies many tokens, prepare its size policy during startup:

```python
verify_bearer = token.prepare_verifier(max_token_chars=8192)
claims = verify_bearer(key, bearer)
```

Setup validates the policy and loads the optional native parser explicitly.
The returned callable keeps the policy and backend references; keys and tokens
are supplied on each call. A compiler-free installation uses the Python path.
Importing only `token` leaves `verify` as the direct Python implementation,
avoiding an extra native-module load. If `digest` already loaded that helper,
`token` selects its native parser once at import. Preparing a verifier later
does not change the public `verify` function. Neither route probes backends or
creates caches on requests. The helper still calls the same BLAKE3 MAC,
constant-time comparison and Base64 decoder.

## Backend

BLAKE3 has no stdlib equivalent; `hashlib` ships BLAKE2b, which is a *different*
algorithm. A silent fallback would fork the digest rather than slow it down, and
every value already addressed by one would stop resolving - so `digest` and
`token` hard-require the `crypto` extra and raise an ImportError naming
`pip install 'unstd[crypto]'` when it is missing. The reviewed minimum is
`blake3>=1.0.10`; no version check runs inside library calls. `tls` is pure stdlib and
imports on a base install.

One-shot hashes, MACs, and key derivation use four workers for exact `bytes`
inputs from 1 MiB, and eight workers from 4 MiB, capped by process CPU
availability on Python 3.13+ (host CPU count on Python 3.12). The cap is sampled
once at import. Smaller inputs and other accepted buffer types retain the serial
backend. The public signatures, algorithms, digest values, truncation rules,
and validation behavior stay the same.

Each parallel call creates a fresh bounded worker pool, avoiding inherited
global pools after `fork()` and retaining no cached keys or key material. If
the backend cannot create its worker pool, hashing retries serially.

When the optional native helper is available, `Stream` batches small exact
`bytes` chunks from exact lists and tuples with at least eight elements. Each
batch is at most 64 KiB, regardless of the total input size. Mutable buffers,
subclasses, and larger chunks reach the backend unchanged, after preceding
bytes have been absorbed. Generators and other iterators retain the eager
Python loop, so they can inspect or update the stream between yields. Streams
remain serial and reusable after a fork. Compiler-free installs retain the
Python implementation. The helper owns no cryptographic implementation and
retains no messages, keys, hashers, or worker pools between calls.

Hex output uses Python's byte-to-hex conversion after BLAKE3 finalization.
[Current paired measurements](../../../bench/crypto-hash-adaptive-results.md)
cover adaptive workers and native stream batching. The
[previous tuning report](../../../bench/crypto-hash-tuning-results.md) records
the 4 MiB crossover and hex-output savings; the
[initial report](../../../bench/crypto-hash-results.md) covers the earlier
16 MiB threshold.

TLS certificate paths use `None` for omission. Explicitly empty `cafile`,
`certfile` or `keyfile` values raise `ValueError`, preventing a configured pin
or client identity from silently disappearing.

Construct one `tls.client_context(...)` per trust/client-identity policy and
reuse it for connections. Finish configuration before first use, keep it
unchanged while connections use it, and rebuild it when certificates or trust
rotate. The factory returns a fresh caller-owned stdlib context; it does not
cache mutable contexts globally. This avoids repeatedly loading roots and PEMs
without adding a wrapper to connection or record processing.

TLS 1.3 is the default floor. Hybrid post-quantum key exchange also requires a
capable linked engine, configuration and peer; a TLS 1.3 connection alone does
not prove a hybrid group was negotiated. Hybrid shares increase handshake wire
size. The library leaves group selection to OpenSSL's policy.

Validate the actual interpreter with
`python -m tests.tooling.runtime` during development and release provisioning;
the [TLS runtime preflight](../../../tests/tooling/runtime/README.md) checks
branch-specific OpenSSL fixes outside library imports and TLS hot calls.

Randomness is the one thing the seam does not re-implement: an opaque bearer
with no claims inside is `unstd.rand.crypto.token_urlsafe()`, which is always
stdlib `secrets` and never seedable. One randomness door in the program, not two.

## Known-answer vectors

[`blake3_reference_vectors.json`](../../../tests/suites/product/crypto/unit/blake3_reference_vectors.json)
is the BLAKE3 team's own published
[test vector set](https://github.com/BLAKE3-team/BLAKE3/blob/master/test_vectors/test_vectors.json),
vendored verbatim, and it drives all three modes - plain hash, keyed hash, and
`derive_key` - across 35 input lengths that span the chunk and subtree
boundaries.

We use the authors' answers rather than our own on purpose. A vector computed by
the code under test proves only that the code agrees with itself; these are the
values a second implementation has to match.

## Before you "fix" a `hashlib` call site

Unifying onto BLAKE3 is safe wherever a digest is ephemeral - a cache key, an
in-memory dict key, a request-scoped id. It is *not* safe where the output was
written down, because the same input hashes differently and every stored value
silently stops matching.

`digest.legacy_blake2b` exists for exactly that case: an identifier already
defined in terms of BLAKE2b, which cannot change algorithm without renaming every
value derived from it. Keeping it inside the seam means the migration stays a
visible decision instead of becoming a reason to import `hashlib` next door and
grow a second digest for new work too.

## One digest, one vocabulary

Content-addressed identifiers are BLAKE3 digests, so they are spelled here too -
`digest.hex(data)` for a content key, `digest.hex(data, 8)` for a short
human-facing id. There used to be a second, identifier-shaped module over the
same three calls; two names for one function is a door to keep in sync, not a
vocabulary. A MAC is not an identifier, so the MAC half lives in `token`.
