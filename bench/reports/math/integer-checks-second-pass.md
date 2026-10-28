---
doc_radar:
  sentinels:
    - file: ../../../src/unstd/math/__init__.py
      contains: ['KERNEL_VERSION >= 2', 'left.size <= 32', 'left.size >= 512']
    - file: integer-checks-second-pass-macos.json
      contains: ['"native_kernel_version": 2', '"native_calls": 1', '"previous_consumer_close"']
    - file: ../../profiles/integer_checks.py
      contains: ['"--require-native"', '"native_math_binary"', '"previous_"']
---

# Exact Integer Checks: Second Pass

We removed scalar NumPy setup, avoided full input copies and fused common
64-bit checks into the existing optional native extension. The public API and
exact signed/unsigned contract are unchanged.

## Measured Calls

The [complete capture](integer-checks-second-pass-macos.json) retains nine
rotating rounds across 48 fixtures on macOS arm64, CPython 3.14.9 and NumPy
2.5.3. Four lanes compare original object arithmetic, saved first-pass code,
current portable code and current native code through both the primitive and
the Consumer's actual `close` function.

| Complete Consumer check | First pass | Second pass | Further speedup | Speedup over object arithmetic |
| --- | ---: | ---: | ---: | ---: |
| Distance three, 8 values | 12.249 µs | 5.902 µs | 2.08× | 1.98× |
| Distance three, 100,000 values | 175.315 µs | 51.569 µs | 3.40× | 152.24× |
| Distance three, 1,000,000 values | 1.609 ms | 657.580 µs | 2.45× | 147.31× |
| Equal, 1,000,000 values | 322.327 µs | 303.524 µs | 1.06× | 280.34× |

The eight-value nonzero-tolerance primitive improved from 6.475 to 0.286 µs,
22.65× over the first pass and 17.34× over object arithmetic. That closes the
first pass's tiny-input regression on this measured case.

For the million-value tolerance check, peak traced allocations fell from
26,001,112 to 634 bytes through the complete Consumer function. Equality fell
from 1,000,565 to 590 bytes; the native kernel creates no working arrays.

## Dispatch And Ownership

Native execution accepts matching signedness, native-endian contiguous 64-bit
arrays of equal arbitrary shape and scalar integer tolerance. It checks signals
between bounded chunks and releases input exports on every return; unaligned
and read-only inputs are supported.

Zero tolerance uses chunked byte comparison. Other tolerances map signed values
into unsigned order and reduce exact distances, avoiding signed subtraction
overflow and float conversion.

The portable implementation uses exact Python integers for scalar-tolerance
inputs up to 32 values. At 512 values and above, matching native-endian integer
widths share unsigned views; other cases retain conversion and the existing
65-bit carry handling. Measurements rejected unsigned views on swapped-endian
inputs, where repeated swapped reads cost more than conversion.

Kernel version 2 advertises the new private method. Older compiled extensions
keep the portable implementation, including its validation and error behavior.

## Losses And Timing Limits

The complete capture retains every loss. Fallback paths pay native admission
before declining; the first-pass comparison includes a 0.883× mixed-carry
primitive, 0.864× wide-tolerance primitive and 0.854× reversed-matrix Consumer
call. These are separate from the successful contiguous native path.

The [21-round recheck](integer-checks-second-pass-recheck-macos.json) retains the
same source and inputs. Mixed-carry primitive speed was 0.968× and swapped-endian
primitive speed was 0.957×; reversed-matrix Consumer wall time measured 0.675×,
with thread CPU at 0.956×. Wide-tolerance primitive thread CPU measured 0.983×.

Shared-host wall timing was noisy. The million-value tolerance Consumer recheck
measured 5.19× over the first pass, while thread CPU measured 3.92×; the primary
capture above remains the reported complete-run result. We do not claim every
fallback improved or every shape reached 100×.

## Reproduce

Run the public helper profile, adding the Consumer source for its complete
validation and failure path.

```sh
python -m bench.profiles.integer_checks --require-native --rounds 9 --output /tmp/integer-checks.json
python -m bench.profiles.integer_checks --consumer-source /path/to/numerics.py --require-native --rounds 9 --output /tmp/consumer-integer-checks.json
```

The [saved source](integer-checks-first-pass.py.txt) is the exact first-pass
module from commit `3876cbb`. The profile verifies its hash, independent integer
oracles, unchanged input storage and actual public/native entries outside timed
regions; it records wrapper, C source and loaded-binary hashes and rejects source
changes during a run.

`--require-native` requires both million-value primary cases to return a real
native result through each selected surface. Native declines remain visible in
other fixtures. Optional `--case` globs narrow confirmation runs without changing
the original fixture set.

## Verification

The math, public API, profile and speed-coverage suites passed 474 tests. Strict
mypy, Ruff, formatting and nine math workload checks passed. Tests cover buffer
lifetime, interruption, integer extremes, byte order and older compiled binaries;
the earlier absolute speed floors remain unchanged.

These are local synthetic CPU workloads. Input construction, kernel startup,
model calls and network latency are excluded. Relative and floating tolerances
remain on the Consumer's existing path; this report does not certify a released
package or a deployed application.
