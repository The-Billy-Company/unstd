Reduced Serde dispatch and allocation overhead for default JSON encoding,
small decoded JSON values, integer snapshot proofs, short Base64 decoding, and
NDJSON writer setup. Public signatures, codec policies, output and error
semantics are preserved. Added a repeatable complete function profile with
output-checked native/fallback benchmarks and explicit API comparisons.
