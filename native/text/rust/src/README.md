# Rust text kernels

The opt-in [`unstd-text` static library](../Cargo.toml) separates numerical code
from its raw-pointer boundary:

- [`core.rs`](core.rs) forbids unsafe Rust: checked code-point slices, wrapping
  bit-vector arithmetic, bounded work and fallible scratch allocation.
- [`lib.rs`](lib.rs) exports the C ABI. Callers must hold exact CPython strings
  and the GIL while borrowed 1-, 2-, or 4-byte views are live; views never escape.
  Status codes are `1` success, `0` refusal, `-1` allocation failure and `-2`
  caught unwinding panic. Keep its `repr(C)` layouts synchronized with
  [`adapter.c`](../adapter.c), which converts statuses to Python values/errors.

Unit tests cover Unicode widths, bit-vector carries, work limits and status
conversion. See the [build and verification commands](../README.md).
