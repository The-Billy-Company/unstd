# Rust text experiment

An opt-in alternative to the [default C kernels](../src/README.md), linked into
the same `unstd.text._text_native` extension. The [`staticlib` crate](Cargo.toml)
has no external crate dependencies; building also needs Rust ≥ 1.74 and C tools.
Default builds do not invoke Cargo. The [comparison report](../../../bench/reports/text/rust-text-results.md)
records the evidence for keeping C as the default.

[`adapter.c`](adapter.c) holds the GIL and exact string arguments;
[`src/lib.rs`](src/lib.rs) borrows their CPython Unicode storage synchronously.
[`src/core.rs`](src/core.rs) forbids unsafe Rust and retains the C work limits,
fallible scratch allocation, and Jaro arithmetic. Allocation failure becomes
`MemoryError`; caught unwinding panics become `SystemError` at the C adapter.

From the repository root:

```console
UNSTD_TEXT_RUST=1 uv sync --reinstall-package unstd
uv run --no-sync python -c "import unstd.text._text_native as n; assert 'Rust text kernels' in n.__doc__"
uv run --no-sync python -m pytest tests/suites/product/text -q
cargo test --manifest-path native/text/rust/Cargo.toml --locked --offline
```

Add `--release` to the Cargo test command to exercise optimized code. Selection
is build-time: unset `UNSTD_TEXT_RUST` and reinstall to return to C. For separate
comparison artifacts:

```console
uv build --wheel --out-dir /tmp/unstd-c
UNSTD_TEXT_RUST=1 CIBUILDWHEEL=1 uv build --wheel --out-dir /tmp/unstd-rust
```

Extract the wheels separately and pass the extension paths to
[`text_native_compare.py`](../../../bench/profiles/text_native_compare.py)
(`--baseline-native`, `--candidate-native`, `--output`). It checks results and
dispatch before timing base-install wrapper calls, and reports kernel timings,
binary size and approximate fresh-process peak RSS separately.
