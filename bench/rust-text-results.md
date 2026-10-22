# Rust text experiment: retain C by default

The Rust text prototype **does not meet the no-overhead requirement**. Ordinary
builds and release wheels retain the original C kernels. Rust is available only
when a developer explicitly builds with `UNSTD_TEXT_RUST=1`; the variable is
never consulted on a scoring call. JSON, cloning, RapidFuzz selection, public
signatures and portable fallback implementations are unchanged by this experiment.

This evaluates one implementation, not the performance ceiling of Rust. The
complete [raw evidence](rust-text-results.json) preserves the source and extension
hashes, all 50 output/route checks, 97 timing rows, every paired batch, noise,
compiler/interpreter metadata and fresh-process peak RSS samples.

## Measurements

Measured on October 21, 2026 (America/Los_Angeles), macOS arm64, CPython 3.13.14,
Apple Clang 21.0.0 and rustc 1.96.0. The C baseline was freshly rebuilt from the
unchanged source using setuptools. The Rust extension was extracted from an
explicitly selected, required-native wheel build. Both C builds used strict
floating-point flags. Rust used the recorded Cargo release profile, including
optimization level 3, one codegen unit, LTO and contained unwinding.

Each workload has five rounds and three repetitions, with C/Rust order
alternating every round and a common calibrated iteration count. The table
reports median paired **Rust time / C time**; below 1 means Rust was faster.
These are complete base-install Python wrapper calls, with payload construction,
correctness checks, route instrumentation and calibration outside timing.

| Workload | C median | Rust median | Paired Rust/C | Pairs with slower Rust |
| --- | ---: | ---: | ---: | ---: |
| Levenshtein, 64 ASCII code points | 744 ns | 543 ns | 0.72× | 0 / 15 |
| Jaro-Winkler, 12 ASCII code points | 546 ns | 636 ns | 1.17× | 15 / 15 |
| Jaro-Winkler, short name | 564 ns | 671 ns | 1.19× | 14 / 15 |
| Jaro-Winkler, below boost threshold | 156 ns | 205 ns | 1.33× | 15 / 15 |
| Jaro-Winkler, single character | 156 ns | 205 ns | 1.33× | 15 / 15 |
| Levenshtein, maximum admitted word work | 9.47 ms | 10.12 ms | 1.07× | 13 / 15 |

Medians of individual implementations and medians of paired ratios are separate
estimators, so their rounded ratios need not coincide. Many batches have large
spreads on this shared host; small differences and fallback timing differences
are inconclusive. The short-Jaro regressions recur across the paired samples,
and the measured evidence is insufficient for a no-overhead replacement. The
raw file retains all workloads, including favorable Rust results.

The extension binary grew from **51,488 to 398,192 bytes**: **7.73×**, an increase
of 346,704 bytes. The prototype links Rust's standard library/unwind support and
specializes the kernels for Unicode storage-width pairs. Default wheel builds
do not link this archive or require Cargo.

Three fresh processes per implementation and large workload produced these
median process high-water marks:

| Workload | C peak RSS | Rust peak RSS |
| --- | ---: | ---: |
| Maximum-work Levenshtein | 29,720,576 bytes | 29,999,104 bytes |
| Maximum-work Jaro | 28,688,384 bytes | 28,901,376 bytes |

RSS includes Python, loaded code, inputs, allocator retention and startup. Each
worker loads only its selected native extension and blocks optional RapidFuzz;
independent oracle allocations happen after both captured readings. The samples
are approximate process observations, not live native allocation accounting or
proof of memory parity. Both cores have bounded scratch requirements; Rust uses
its system allocator rather than CPython's small-object allocation pools.

## Safety and compatibility

The numerical core forbids unsafe Rust and replaces manual buffer indexing and
freeing with checked slices and owned, fallibly allocated buffers. A small C
adapter preserves CPython calling conventions and retains the exact string
arguments and GIL. Documented unsafe Rust creates borrowed views of Python's
immutable 1-, 2-, or 4-byte code-point storage. No input transcoding, string
copies, Python callbacks or additional Python/native crossings are introduced.
Panics cannot unwind across the C boundary. This reduces the manual-memory
surface, while retaining a trusted CPython/FFI boundary.

The prototype preserves lone surrogates, NULs, all storage-width combinations,
wrapping multiword carries, exact Jaro floating-point arithmetic, work budgets,
`NotImplemented` refusals, foreign-value handling and the Python fallbacks.
Installed RapidFuzz bindings still bypass the bundled base-install scorers.

Validation completed locally:

- Eight Rust unit tests and Clippy with warnings denied.
- All 82 native text tests on CPython 3.12.13, 3.13.14 and 3.14.6.
- 337 default text, benchmark and package-description gates passed; three
  performance-marked tests were deselected.
- All 56 artifact tests passed, including a Rust wheel rebuilt offline from its
  sdist and installed in a pristine dependency-free consumer, missing/invalid
  Cargo/Rust fallback, strict build failures, stale editable cleanup and default
  builds without Cargo.
- Scoped Ruff/format checks and strict mypy for the comparison harness passed.

The ordinary C source and the checkout's default extension stayed byte-for-byte
unchanged during the experiment. CI adds explicit Rust compatibility builds on
Linux, macOS and Windows and instruments the C adapter with ASan/UBSan; those
remote jobs have not been executed locally, and their flags do not instrument
the stable Rust core.

## Reproduce

See [the prototype instructions](../native/text/README.md) to build and extract
separate C and Rust wheels, then run:

```console
python bench/text_native_compare.py \
  --baseline-native /tmp/c/unstd/text/_text_native.cpython-313-darwin.so \
  --candidate-native /tmp/rust/unstd/text/_text_native.cpython-313-darwin.so \
  --baseline-source src/unstd/text/_text_native.c \
  --candidate-source native/text/Cargo.toml \
  --candidate-source native/text/Cargo.lock \
  --candidate-source native/text/adapter.c \
  --candidate-source native/text/src/lib.rs \
  --candidate-source native/text/src/core.rs \
  --rounds 5 --repetitions 3 --memory-repetitions 3 \
  --output /tmp/rust-text-results.json
```

Use the extension suffix for the selected interpreter/platform. The harness
rejects missing extensions, incorrect values/types/float bits, and native calls
that silently refuse accepted workloads. The
earlier [native kernel audit](native-results.md) compares Python with C and
cannot establish Rust parity.
