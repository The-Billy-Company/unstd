# Token and TLS security contracts

`test_adverse.py` probes wrong keys, corrupted claims, tag widths, malformed envelopes, comparison behavior, and digest mode boundaries. `test_token_boundary.py` constructs independently authenticated envelopes and checks canonical payload/signature spelling, byte mutations, Unicode junk, and exact opaque claims.

The token boundary suite also checks the default inclusive 8192-character
envelope limit, explicit larger/unbounded limits, rejection before inspecting
oversized text, configuration errors, every terminal Base64 bit pattern,
malformed-payload/invalid-key ordering, subclasses, and decode-after-MAC behavior.

`test_token_native.py` exercises both Python and native parsing, callback/GC
ownership, request-key release, interruptible large scans, explicit prepared
policies, compiler-free fallback, and both module import orders. It asserts
that token-only imports avoid an extra native load and preparation does not
rewire the public verifier.

`test_tls.py` checks verified contexts, protocol floors, pinned trust, and certificate/key loading. Token tests require BLAKE3; TLS works without it, with cryptography needed to mint PEM fixtures. Real peer handshakes live in [the pentest suite](../../../assurance/security/pentest/README.md).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/crypto/security -q
```

See [the module test overview](../README.md) for related evidence.
