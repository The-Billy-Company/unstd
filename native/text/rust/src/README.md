# Rust text kernels

The [`Cargo.toml`](../Cargo.toml) static library separates numerical code from
its raw-pointer boundary:

| File | Responsibility |
| --- | --- |
| [`core.rs`](core.rs) | Forbid unsafe Rust; implement bounded Levenshtein/Jaro over checked code-point slices, with wrapping bit-vector arithmetic and fallible scratch allocation. Embedded tests cover results, bit-vector carries, Unicode and work limits. |
| [`lib.rs`](lib.rs) | Export `unstd_text_distance` and `unstd_text_jaro`, borrow CPython storage for one synchronous call, and turn kernel outcomes or caught unwinding panics into C result structs. Embedded tests cover widths and failure status conversion. |

Callers must hold exact CPython strings and the GIL while borrowed 1-, 2-, or
4-byte views are live; views never escape. Status codes are `1` success, `0`
refusal, `-1` allocation failure and `-2` caught unwinding panic. Keep `repr(C)`
layouts and these codes synchronized with [`../adapter.c`](../adapter.c).

Changes to arithmetic, allocation or work limits must preserve the
[default C implementation](../../src/adapter.c)'s results and refusal behavior.
See the [experiment guide](../README.md) for build and verification commands.
