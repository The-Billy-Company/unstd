# Security testing

Test the boundaries unstd owns: attacker-controlled tokens, serialized input,
regexes, process arguments, filesystem entries and TLS peers. The existing
behavioral tests already cover these boundaries; new coverage should close a
specific gap rather than repeat an attack under another directory.

| What must hold | Canonical evidence |
| --- | --- |
| Only authentic, canonical bearer envelopes release the exact signed bytes | [Crypto security](../../product/crypto/security/test_token_boundary.py), independent BLAKE3 vectors in crypto unit tests |
| TLS verifies trust, identity, client credentials and the protocol floor | [Real loopback peers](pentest/README.md), crypto TLS policy tests |
| Untrusted encodings and YAML tags cannot bypass validation or construct Python objects | Product serde/YAML security tests and reviewed fuzz replay |
| Hostile regexes exercise the claimed native path within a worker deadline | Product rex security tests; fallback behavior is explicitly distinguished |
| Shell syntax stays literal; atomic scratch files and default traversal resist collisions/symlink escape | Product proc/fs integration tests and owning symlink-race regressions |
| Authentication uses the approved comparison primitive; secure randomness stays on the CSPRNG and fails on entropy loss | [Runtime primitive canaries](structure/README.md) |

Keep the three security evidence kinds separate:

- `security-contracts`: module-owned outcomes under
  `product/<module>/security`.
- `pentest`: actual TLS peers under `assurance/security/pentest`.
- `security-structure`: a small set of live primitive-selection and decision
  canaries under `assurance/security/structure`.

Process/filesystem integration and confirmed regressions keep their existing
owners. Fuzz, chaos and resource probes reuse their existing bounded workers;
this lane does not need another harness, scanner, campaign CLI or CI job.

Run the focused security and process/filesystem boundary checks:

```console
uv run --no-sync pytest --require-extras -q -m 'security or pentest or security_structure or integration'
uv run --no-sync python -m tests.tooling run security-structure -- --require-extras -q
```

The normal `pytest --require-extras` CI matrix also includes these tests,
owning regressions and finite fuzz replay. The all-extra and dependency-floor
legs require backends. Optional local/base runs skip only the MAC canaries when
BLAKE3 is absent; secure-random canaries and TLS remain executable.

Pair rejections with successful controls and independent expected values.
Canaries must observe actual primitive use and its decision, not search source
strings. Do not infer constant-time execution from wall-clock comparisons or
CSPRNG strength from token uniqueness. These tests do not attest deployed-service
authorization, certificate revocation or compiled dependency internals.
