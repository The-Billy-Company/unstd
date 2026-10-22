# Rust text kernels

This directory implements the opt-in [`unstd-text` static library](../Cargo.toml):

- [`core.rs`](core.rs) forbids unsafe Rust and implements bounded Levenshtein
  distance and Jaro similarity, with checked indexing and fallible allocation.
  Unit tests cover algorithm results, Unicode values, and work limits.
- [`lib.rs`](lib.rs) exports the C ABI, borrows CPython's Unicode storage for
  synchronous calls, and converts success, fallback, allocation failure, and
  caught panics into status codes. Its tests exercise the boundary and Unicode
  storage widths. [`adapter.c`](../adapter.c) owns the Python-facing checks and
  converts those statuses to Python results or exceptions.

From the repository root, run
`cargo test --manifest-path native/text/Cargo.toml --locked --offline`; add
`--release` to test the optimized build. See the [experiment guide](../README.md)
for building the Python extension and running compatibility checks.
