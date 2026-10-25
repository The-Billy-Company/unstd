# Default C text kernels

[`_text_native.c`](_text_native.c) implements `distance` and `jaro` in
`unstd.text._text_native`. Calls retain the GIL and borrow immutable CPython
1-, 2-, or 4-byte string storage without transcoding; NULs and lone surrogates
remain code points. No Python callbacks run inside either kernel.

Non-exact strings and work outside these limits return `NotImplemented` to
[`fuzz.py`](../../../src/unstd/text/fuzz.py); allocation failure raises
`MemoryError`:

| Kernel | Work limits |
| --- | --- |
| Levenshtein, both inputs nonempty | Shorter length ≤ 65,536; longer ≤ 262,144; longer × ceil(shorter / 64) ≤ 4,194,304; shorter input has ≤ 256 distinct code points |
| Jaro | Each length ≤ 65,536; length product ≤ 4,194,304 |

Levenshtein uses multiword bit vectors and stack scratch for patterns up to
64 code points. Jaro preserves Python's separate double operations and integer
transposition floor; the build hook disables fast-math and contraction.
The [Rust prototype](../rust/README.md) implements the same refusal protocol.
