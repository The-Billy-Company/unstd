# JSONX compatibility and native memory safety

This page retains the first-pass qualification. The
[second-pass qualification](jsonx-second-safety.md) covers the subsequent float
writer, owned strict snapshots, forced allocation failures and corrected
attribute-cache retention.

The final kernel retains exact stdlib output and fallback behavior. Wide and
indented native proofs inspect at most **262,144 values**, including every
repeated alias, and at most **16 container levels**. The original private
`encode` helper retains its **65,536-value** limit. Exceeding a proof budget
selects stdlib; it does not reject a supported JSON value.

Review caught an unbounded validation risk in an intermediate implementation:
a tiny graph of shared lists could require an enormous number of visits. The
fixed visit budget and a subprocess regression now prevent that validation
stall. The regression calls native helpers directly so it never asks stdlib
to allocate the graph's enormous serialized representation.

## Checks retained in the repository

- [Encoding guarantees](../../../tests/suites/product/serde/parity/test_jsonx_encode_guarantees.py):
  trace/profile mutations, exact option types and subclasses, callback counts
  and mutations, large integers and digit limits, surrogate errors, partial
  stream writes, recursion headroom, 128 KiB thread stacks, IEEE float patterns,
  cache collisions, visit limits and shared graphs.
- [Native memory checks](../../../tests/suites/product/serde/parity/test_jsonx_native_memory.py):
  large escaped buffers, quote/backslash runs, the 128-character decoder prefix
  boundary, malformed UTF-8, numeric scratch-buffer boundaries and repeated-call
  allocation retention.
- [Wide and indented output](../../../tests/suites/product/serde/parity/test_jsonx_native_indent.py)
  and [Unicode escapes](../../../tests/suites/product/serde/parity/test_jsonx_text_fast.py):
  exact output and observable native-path use.
- Existing serde parity, property, security, unit, lifecycle and streaming tests
  continue to exercise stdlib fallback behavior alongside the new coverage.

The allocation soaks have the existing `resources` marker. They are deliberately
selected explicitly; ordinary compatibility runs exclude that lane. Run on
Python 3.12 to include its native float-normalization cache.

```sh
PYTHONMALLOC=debug python -m pytest tests/suites/product/serde -q
python -m pytest tests/suites/product/serde/parity/test_jsonx_encode_guarantees.py -q
python -m pytest tests/suites/product/serde/parity/test_jsonx_native_memory.py -m resources -q
```

## Optimized kernels with Python's debug allocator

All runs used the final bounded C source and the normal optimized extension.

| Python | Complete serde suite plus existing consumer benchmark gates | Additional memory checks, including resources |
| --- | ---: | ---: |
| 3.12.15 | 2,921 passed, 65 skipped | 116 passed |
| 3.13.16 | 2,931 passed, 55 skipped | 115 passed, 1 skipped |
| 3.14.8 | 2,953 passed, 33 skipped | 115 passed, 1 skipped |

The portable source tree, with no native extension, passed another **2,635**
parity/property/unit checks with seven optional-module skips. A reordered
13-case run verified that the new fast-path spies leave later stdlib fallback
spies intact. Ruff, formatting and focused mypy checks passed.

## AddressSanitizer and UndefinedBehaviorSanitizer

[Raw safety evidence](jsonx-native-safety.json) retains exact compiler and runtime
options, commands, Python versions, loaded binary/source hashes, per-run counts,
stress results and allocation samples. Every recorded run exited successfully;
there were no address or undefined-behavior diagnostics.

The campaign covered complete serde suites on Python 3.12–3.14, additional
bounded stress, the retained memory tests and their explicit resources lane.
Stack-use-after-return mode also covered the Python 3.12 float normalization and
cache path, and the Python 3.14 native paths. Retained traced allocations were at
most **936 bytes** after 300 measured calls, within the permanent **4 KiB**
plateau bound.

The local campaign instruments this extension and its adjacent headers on
macOS arm64. CPython and third-party backends are not rebuilt with sanitizers.
Process-lifetime leak detection is disabled, matching the existing sanitizer CI
policy; operation-local retention is checked separately. These are concrete
checks of the tested source and environments, rather than an all-platform proof.
