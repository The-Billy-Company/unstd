# Bounded token verification and patched crypto tooling

`token.verify` now rejects encoded envelopes longer than 8192 characters before
scanning, slicing, decoding or hashing. Pass a larger positive
`max_token_chars=` limit, or explicitly `None` after enforcing a request bound,
for bulk envelopes. Token bytes, MACs, canonical encoding rules and key errors
within the bound are unchanged. Verification authenticates before decoding
claims and checks canonical Base64 lengths and terminal bits directly.

`token.prepare_verifier(max_token_chars=...)` explicitly prepares a reusable
size policy and optional native envelope parser at startup. It retains no
signing keys or request data. Token-only imports keep the direct Python path;
imports after `digest` reuse an already loaded helper. This avoids imposing
native loading costs on token-only users or deferring setup to a first request.

The crypto extra requires `blake3>=1.0.10` for reviewed upstream correctness and
dependency fixes. Worker caps respect process CPU availability on Python 3.13+
without per-call probes. Existing algorithms, native stream batching and exact
`secrets` aliases remain in place.

Development uses CPython 3.13.16 with patched OpenSSL 3.5.9. CI, release tooling
and wheel tests run a branch-aware TLS-runtime preflight; no version checks are
added to crypto hot calls. Package support still starts at Python 3.12. TLS
contexts remain independently configured; callers may reuse a configured context
without sharing mutable policy across unrelated callers.
