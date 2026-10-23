# Native text metrics

[`src/`](src/README.md) contains the default C kernels for bounded Levenshtein
distance and Jaro similarity. [`rust/`](rust/README.md) contains an explicitly
selected prototype with its own adapter, Cargo manifest, and source tree.

Both build into the existing private `unstd.text._text_native` extension. Users
continue to call [`unstd.text`](../../src/unstd/text/README.md); wrapper eligibility,
fallible allocation, Unicode behavior, and fallback contracts stay the same.
Ordinary builds use C. Set `UNSTD_TEXT_RUST=1` at build time to select Rust.
