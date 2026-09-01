# `unstd.ids`

Identifier generation — one hasher and one UID strategy shared across the
program. `hash` is a thin caller of `unstd.crypto.digest`, so there is
exactly one BLAKE3 implementation with an identifier-shaped vocabulary over it.

## Modules

| File      | Role                                                                                                                                               |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hash.py` | `sum_` / `hex_n` / `content_hash` over `unstd.crypto.digest`. The MAC half moved to `unstd.crypto.token`.                                          |
| `uid.py`  | `new` / `new_hex` mint a UUIDv7 (RFC 9562); `derive` computes a v5; `parse` reads one back. `ids` extra → `uuid-utils`; stdlib fallback otherwise. |

## Minted vs derived

`new()` and `derive()` fail in opposite directions, so they are used for
opposite things. A minted v7 must never repeat. A derived v5 must never
_change_: the same namespace and name give the same id forever, which is how a
Gmail thread or an email correspondence names its own conversation with no
lookup table — and therefore why its past output is load-bearing. Changing a
namespace, changing how a caller normalizes the name, or changing the digest
underneath renames every identity derived before it, so a differing value is a
defect rather than a new expectation.

`derive` is plain RFC 4122 v5, so an implementation in any other language agrees
with it byte for byte — which is the point, when two planes read and write the
same derived id column. The vectors are pinned in `tests/ids/test_uid.py`.

## Backend

The `ids` extra (`pip install 'unstd[ids]'`) provides `blake3` + `uuid-utils`.
`hash` fails loud without `blake3` (BLAKE2b ≠ BLAKE3 — a silent fallback would
fork the digest), raising through `unstd.crypto`, which names the `crypto` extra;
both extras pin the same version. `uid` falls back to a spec-correct stdlib
UUIDv7 generator.
