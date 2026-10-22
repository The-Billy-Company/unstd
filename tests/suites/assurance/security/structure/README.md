# Security primitive canaries

Status: **active**. Run through the existing suite runner:

```console
uv run --no-sync python -m tests.tooling run security-structure -- --require-extras -q
```

`test_mac_primitives.py` observes real `hmac.compare_digest` calls for byte/text
equality, raw/hex MAC verification and canonical bearer verification. Independent
raw BLAKE3 and stdlib Base64 define the full expected operands. Authentic and
forged tags exercise both outcomes; a primitive forced to refuse an authentic
tag proves callers honor its decision. This replaces source-substring checks
that could accept a dead call or an ignored result.

`test_random_primitives.py` pins all six public secure-random exports to stdlib
`secrets` before and after seeding. Controlled entropy proves exact token
encoding, failure propagation and recovery through the live stdlib provider.
It replaces collision-based security assertions with deterministic observations.

These are finite ordinary tests, not timing benchmarks or statistical randomness
tests. BLAKE3 absence skips MAC canaries only in optional local/base runs;
`--require-extras` makes missing capabilities fatal. RNG canaries need no extra.
The ordinary CI matrix, dependency-floor and fallback runs already discover the
active inventory owner. Behavioral security evidence remains in
[its existing owners](../README.md).
