# Bearer token replay inputs

These version-1 envelopes drive the [token target](../../targets/tokens.py) and
`unstd.crypto.token`. Byte 0 modulo three selects arbitrary UTF-8/surrogate-text
verification, authentic-token generation plus tampering, or independently signed
malformed payload spellings. Remaining bytes are attacker text or opaque claims
entropy; generated claims are capped at 8,192 bytes. Empty input verifies empty
text. Fixed public test keys are fixtures, never secrets.

Retained cases cover authentic opaque claims, wrong spellings, padding/pad bits,
separator/signature changes, invalid text and the generation bound. Independent
stdlib Base64 and raw keyed BLAKE3 enforce two canonical unpadded Base64url
segments and the exact signature. Arbitrary text must fail closed without
exceptions. No JSON, expiry, audience or application authorization is inferred.

The target is installed-only and requires `blake3`. After
[dependency preparation](../../README.md), run from the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target tokens --backend installed
```

`seeds.json` authenticates complete envelopes under the
[shared corpus rules](../README.md), including each case's reviewed provenance.
