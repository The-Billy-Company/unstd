# Native JSON dispatch

[`src/`](src/README.md) contains the private guarded JSON kernel used by
[`unstd.serde`](../../src/unstd/serde/README.md). Its installed extension remains
`unstd.serde._json_native`. Public `jsonx` and `ndjson` modules own compatibility,
backend eligibility, and fallback behavior.
