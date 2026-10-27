# Default C text adapter

[`adapter.c`](adapter.c) is the complete C implementation and module binding
for `unstd.text._text_native`. `distance` uses multiword bit vectors with stack
scratch for patterns up to 64 code points; `jaro` matches within sliding windows
and applies Python's integer transposition floor.

Both calls hold the GIL and borrow immutable CPython 1-, 2-, or 4-byte string
storage without transcoding. Embedded NULs and lone surrogates remain code
points; no Python callbacks run inside the kernels.

| Kernel | Work limits |
| --- | --- |
| Levenshtein | Equal prefixes/suffixes are removed using at most 4,194,304 code-point pair comparisons, counting mismatches across both ends. The remaining shorter length ≤ 65,536; longer ≤ 262,144; longer × ceil(shorter / 64) ≤ 4,194,304; shorter remainder has ≤ 256 distinct code points. An empty remainder returns the other remainder's length directly. |
| Jaro | Each length ≤ 65,536; length product ≤ 4,194,304. |

Non-exact strings or exceeded work limits return `NotImplemented` to
[`fuzz.py`](../../../src/unstd/text/fuzz.py); allocation failure raises
`MemoryError`. Maintain that distinction when changing budgets or scratch
storage. Jaro preserves Python's separate double operations: the
[build hook](../../../hatch_build.py) disables fast-math and contraction.
The [Rust alternative](../rust/README.md) must retain the same contracts.

Shared-end removal borrows string storage without allocating substrings. Long
strings with small differences can therefore stay in the native kernel even
when their original lengths or alphabets exceed its residual work limits. The
portable fallback also removes equal ends before allocating bit masks or DP
rows. [Paired measurements](../../../bench/reports/text/affixes.md) compare both
paths with their original implementations.
