# Native crypto helpers

[`src/`](src/README.md) builds `unstd.crypto._stream_native`: bounded stream
batching and canonical bearer-envelope parsing. [`digest.py`](../../src/unstd/crypto/digest.py)
and [`token.py`](../../src/unstd/crypto/token.py) select these helpers and retain
Python fallbacks. BLAKE3, MAC computation and constant-time comparison remain
in their existing backends.
