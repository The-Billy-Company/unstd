---
doc_radar:
  sentinels:
    - file: src/unstd/ids/uid.py
      contains: ['def new() -> str:', 'def _reset_after_fork()']
---

# `unstd.ids`

Identifier generation — one UID strategy shared across the program.
Content-addressed ids are BLAKE3 digests and live with the digest:
`unstd.crypto.digest.hex(data)` (or `hex(data, 8)` for a short id).

## Source ownership

This flat leaf keeps identifier generation, derivation, and parsing behind the
single `uid` module.

| File      | Role                                                                                                                                               |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`__init__.py`](__init__.py) | Package documentation; importing the package leaves `uid` backend selection deferred. |
| [`uid.py`](uid.py) | `new` / `new_hex` mint a UUIDv7 (RFC 9562); `derive` computes a v5; `parse` reads one back. Owns optional backend selection and fallback clock, counter, entropy, and fork state. |

Run the [identifier contracts](../../../tests/suites/product/ids/README.md) from
the repository root with `uv run --no-sync pytest tests/suites/product/ids -q`.

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
same derived id column. The vectors are pinned in
[`test_uid.py`](../../../tests/suites/product/ids/unit/test_uid.py).

## Backend

The `ids` extra (`pip install 'unstd[ids]'`) provides `uuid-utils`. BLAKE3 hashing
lives in `unstd.crypto.digest` behind the `crypto` extra. `uid` falls back to stdlib `uuid.uuid7` on Python 3.14+, and to a spec-correct
hand-rolled RFC 9562 generator before that.

The fallback uses a 12-bit monotonic counter plus 62 fresh random bits. CPython
3.14's generator uses a 42-bit counter. Both emit the UUIDv7 wire format and
order sequential mints, with different counter layouts and generation costs.

The hand-rolled generator reserves counters under a thread lock. After a POSIX
fork, the child gets a fresh unlocked counter and reseeds from its own entropy;
the parent's state and selected backend are unchanged. Reservation order can
differ from return order when calls overlap. Controlled histories and the
inherited-lock regression live under `tests/suites`.
