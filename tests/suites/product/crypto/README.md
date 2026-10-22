# Cryptography contracts

Contracts for `unstd.crypto`: BLAKE3 vectors, authenticated token envelopes, and verified TLS context policy.

- [unit/](unit/README.md): published digest, MAC, and key-derivation vectors.
- [security/](security/README.md): token rejection boundaries and TLS policy.
- [regression/](regression/README.md): reserved location for confirmed cryptography defects.

Digest and token tests require the `crypto` extra. TLS policy runs without BLAKE3; PEM-generation cases use the development dependency cryptography. Real peer handshakes live in [the pentest suite](../../assurance/security/pentest/README.md).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto -q
```

See [the module documentation](../../../../src/unstd/crypto/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
