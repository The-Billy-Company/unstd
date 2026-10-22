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
| `_stream_native.c` | Optional bounded batching of small immutable chunks before the BLAKE3 backend absorbs them. |
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

## Backend

BLAKE3 has no stdlib equivalent; `hashlib` ships BLAKE2b, which is a *different*
algorithm. A silent fallback would fork the digest rather than slow it down, and
every value already addressed by one would stop resolving - so `digest` and
`token` hard-require the `crypto` extra and raise an ImportError naming
`pip install 'unstd[crypto]'` when it is missing. `tls` is pure stdlib and
imports on a base install.

One-shot hashes, MACs, and key derivation use four workers for exact `bytes`
inputs from 1 MiB, and eight workers from 4 MiB, capped by the reported CPU
count. Smaller inputs and other accepted buffer types retain the serial
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
