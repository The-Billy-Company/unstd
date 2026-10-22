# Rust text experiment

This is an explicitly selected prototype. Ordinary builds and published wheels
continue to compile `src/unstd/text/_text_native.c`; they never invoke Cargo.
The experiment must meet the project's no-overhead requirement before it can
replace that implementation. See the [comparison report](../../bench/rust-text-results.md).

## Implementation

`src/core.rs` forbids unsafe Rust. It implements the existing bounded
Levenshtein and Jaro algorithms using checked slices, wrapping integer
arithmetic, and buffers with fallible allocation. The short-distance path keeps
its scratch storage on the stack. Jaro retains Python's individual floating-point
operations and integer transposition floor.

`adapter.c` retains the CPython calling convention, exact-type checks, GIL,
strongly held arguments, interpreter support, and result/error conversion.
`src/lib.rs` is the small unsafe boundary: it borrows the immutable 1-, 2-, or
4-byte Unicode storage for the synchronous call. It does not transcode or copy
the strings, so lone surrogates and embedded NULs remain code points. Panics
are caught before returning through C, and allocation errors become
`MemoryError`. The core retains the C implementation's work limits and refusal
protocol, so the existing Python wrappers choose the same fallback paths.

The crate has no external dependencies. Its use of safe Rust reduces manual
buffer and lifetime mistakes in these kernels; the CPython boundary, Rust
standard library, Python interpreter and other installed extensions remain
trusted implementation dependencies.

## Build and verify

Install a Rust toolchain and the usual C compiler, then explicitly rebuild:

```console
UNSTD_TEXT_RUST=1 uv sync --reinstall-package unstd
uv run --no-sync python -c "import unstd.text._text_native as n; assert 'Rust text kernels' in n.__doc__"
uv run --no-sync python -m pytest tests/suites/product/text -q
cargo test --manifest-path native/text/Cargo.toml --locked --offline
cargo test --manifest-path native/text/Cargo.toml --locked --offline --release
```

The environment variable is read when building, not on each scoring call. A
cached or already installed wheel does not change implementations just because
the variable is set. To return an editable checkout to C, unset the variable
and rebuild with `uv sync --reinstall-package unstd`.

For isolated comparison artifacts, build two wheels in separate directories:

```console
uv build --wheel --out-dir /tmp/unstd-c
UNSTD_TEXT_RUST=1 CIBUILDWHEEL=1 uv build --wheel --out-dir /tmp/unstd-rust
```

Extract each wheel to a separate directory and pass its text extension path to
`bench/text_native_compare.py --baseline-native PATH --candidate-native PATH
--output /tmp/text-comparison.json`. The harness validates results and native
dispatch before timing complete base-install Python calls. Direct kernel calls,
all timing samples, noise, source/binary hashes and sizes, and approximate fresh
process peak RSS are recorded separately. Python-only profiling results in
`bench/native-results.md` are not a C-versus-Rust comparison.
