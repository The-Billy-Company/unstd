# `unstd.crypto`

One cryptographic seam: BLAKE3 digests, keyed-BLAKE3 MACs, and a verifying TLS
client policy. The point of a seam is that there is exactly one of it - no call
site reaches past this package into `hashlib`, and no call site invents its own
constant-time compare.

```python
from unstd.crypto import digest, token, tls

digest.hex_(b"content")  # BLAKE3-256, 64 hex chars
digest.derive_key("myapp session key v1", km)  # a domain-separated subkey
token.mint(key, claims_json_bytes)  # payload.tag bearer
token.verify(key, bearer)  # claims bytes, or None
tls.client_context(cafile="private-root.pem")
```

Install with the extra: `pip install 'unstd[crypto]'`.

## Modules

| File        | Role                                                                                                      |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| `digest.py` | BLAKE3-256 - `sum_` / `hex_` / `hex_n` / `of_parts` / `Stream`, `derive_key`, constant-time `equal`, and the `legacy_blake2b` escape hatch. |
| `token.py`  | Keyed-BLAKE3 `mac` / `verify_mac`, opaque bearers, and the claims envelope (re-exports `equal`).           |
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
gives a wholly unrelated key.

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
every value already addressed by one would stop resolving - so this package hard
-requires the `crypto` extra and raises an ImportError naming
`pip install 'unstd[crypto]'` when it is missing.

Randomness is the one thing the seam does not re-implement: `token.opaque`
delegates to `unstd.rand.crypto`, which is always stdlib `secrets` and never
seedable. One randomness door in the program, not two.

## Known-answer vectors

`tests/crypto/blake3_reference_vectors.json` is the BLAKE3 team's own published
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

## Relationship to `unstd.ids.hash`

`ids.hash` is a thin caller of `digest` - three functions (`sum_`, `hex_n`,
`content_hash`) that exist so identifier call sites keep their identifier
vocabulary. One implementation, two named doors; a MAC is not an identifier, so
the MAC half lives in `token`.
