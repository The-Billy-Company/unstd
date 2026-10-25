# Rust text experiment

Build-time alternative to [`../src/adapter.c`](../src/adapter.c), producing the
same `unstd.text._text_native` extension. The crate has no external crate
dependencies; building needs Rust ≥ 1.74 and C tools. Default builds never invoke
Cargo; the [comparison report](../../../bench/reports/text/rust-text-results.md)
records the evidence for keeping C as the default.

| File | Responsibility |
| --- | --- |
| [`adapter.c`](adapter.c) | Check exact CPython strings, hold the GIL and convert Rust results/status codes to Python objects or exceptions. |
| [`Cargo.toml`](Cargo.toml) | Declare the static library, Rust floor and optimized release/unwinding settings. |
| [`Cargo.lock`](Cargo.lock) | Lock the dependency-free crate for offline builds. |
| [`src/lib.rs`](src/lib.rs) | Export the C ABI, borrow CPython Unicode storage synchronously and contain unwinding panics. |
| [`src/core.rs`](src/core.rs) | Implement bounded distance/Jaro algorithms in safe Rust with fallible scratch allocation and embedded unit tests. |

Keep `adapter.c` and `src/lib.rs` layouts/status codes synchronized. Borrowed
views must not outlive the C call; allocation failure becomes `MemoryError`,
while a caught unwinding panic becomes `SystemError`. See
[`src/README.md`](src/README.md) for the boundary contract.

From the repository root:

```console
UNSTD_TEXT_RUST=1 uv sync --reinstall-package unstd
uv run --no-sync python -c "import unstd.text._text_native as n; assert 'Rust text kernels' in n.__doc__"
uv run --no-sync python -m pytest tests/suites/product/text -q
cargo test --manifest-path native/text/rust/Cargo.toml --locked --offline
```

Add `--release` to test optimized Rust code. Selection is build-time: unset
`UNSTD_TEXT_RUST` and reinstall to return to C. For isolated comparisons, build
separate C/Rust wheels, extract their extensions, then pass those paths to
[`text_native_compare.py`](../../../bench/profiles/text_native_compare.py)
(`--baseline-native`, `--candidate-native`, `--output`). It checks results and
wrapper dispatch before recording timings, binary sizes and approximate fresh-process RSS.
