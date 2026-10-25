# Native crypto helpers

[`src/adapter.c`](src/adapter.c) contains the CPython binding and both helpers
in `unstd.crypto._stream_native`: stream chunk batching and canonical bearer
envelope parsing. [`src/README.md`](src/README.md) describes their call and
ownership contracts.

[`digest.py`](../../src/unstd/crypto/digest.py) selects batching for materialized
streams. [`token.py`](../../src/unstd/crypto/token.py) can bind the parser when
the extension is already loaded or through `prepare_verifier`; a token-only
import keeps the Python path. The wrappers retain fallbacks when compilation
or input eligibility prevents native dispatch.

The C source owns no hash algorithm: BLAKE3, MAC computation and constant-time
comparison stay with their existing backends. Changes must preserve callback
order, eager iterator behavior, and authentication before payload decoding;
[crypto tests](../../tests/suites/product/crypto/README.md) exercise these boundaries.
