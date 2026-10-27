# Kernel 2 release qualification

**The complete-decoder 100× target is still unmet on both operating systems.**
This round ships a smaller native improvement and evaluates the broader
current project CPU workflow without changing Consumer application files.

## Shipped change

The generic validator uses an exact unsigned maximum of sign-cleared float32
bits for NEON chunks of at least 64 KiB. A maximum below `0x7f800000` proves
that every value is finite, including negative values and all signaling/quiet
NaN encodings. The existing path handles smaller chunks; SSE2, float64,
float16, scalar fallback, metadata validation, cancellation and immutable
ownership retain their contracts. No dependency, thread pool or public API was
added. The private mechanism identifier is `KERNEL_VERSION = 2`.

The [direct same-compiler comparison](native-mechanisms.md) found a 13–20%
complete-call improvement at medium sizes on macOS. A
[forced-build Linux comparison](native2-linux-direct-forced.json), with
distinct verified version-1/version-2 binaries and identical GCC flags, found
an 8–16% improvement at those sizes and wins in all nine paired rounds at each
size of at least 64 KiB. Tiny routes remained tied. The small-input result motivated
the explicit threshold. Wider SME vectors, parallel dispatch, Accelerate,
NumExpr and alternative NumPy constructors were evaluated separately; their
results and limitations are retained in the [investigation](README.md).

The first Linux direct control is retained as an
[invalid comparison](native2-linux-direct.json): timestamp checks reused the
same prebuilt version-2 binary for all three arms. Its numbers do not establish
a kernel regression or speedup. The corrected run forces compilation and
[verifies different binary hashes and versions before timing](native2-linux-direct-forced-builds.json).

## Complete single-frame decoder, installed wheels

Both arms allocate the exact captured result class and preserve all caller
guards, numeric admission, immutable output and device state. Nine rounds
alternate order with 50 ms target batches. Output allocation and refcount
cleanup are timed; setup and independent oracles are excluded. Each call scans
the full floating payload; no validity/result cache is used.

| Rows × 128 columns | macOS before → after | Median gain | Conservative gain | Linux before → after | Median gain | Conservative gain |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 3.623 → 1.060 µs | 3.42× | 2.42× | 3.614 → 1.138 µs | 3.18× | 2.28× |
| 32 | 4.170 → 1.190 µs | 3.50× | 3.39× | 4.426 → 1.347 µs | 3.29× | 1.91× |
| 128 | 6.543 → 1.519 µs | 4.31× | 4.17× | 8.511 → 2.156 µs | 3.95× | 2.03× |
| 512 | 13.733 → 3.135 µs | 4.38× | 3.31× | 90.627 → 3.569 µs | 25.39× | 17.65× |
| 8,192 | 175.112 → 39.669 µs | 4.41× | 3.96× | 1,291.065 → 51.314 µs | 25.16× | 20.45× |
| 65,536 | 1,491.452 → 343.659 µs | 4.34× | 3.94× | 3,941.948 → 444.708 µs | 8.86× | 7.61× |

Conservative gain is the fastest baseline sample divided by the slowest
candidate sample. Every case fails both the median and conservative 100×
checks. The large Linux copy/allocation cost is visible in its baseline and
must not be substituted for macOS. Original large-frame controls remain in the
comparison; the captured current recipe limits an individual document to 512
rows.

- [macOS raw evidence](native2-macos-wheel-performance.json) and
  [CLI result](native2-macos-wheel-performance.txt): macOS 26.6 arm64,
  CPython 3.14.9, NumPy 2.5.3.
- [Linux raw evidence](native2-linux-wheel-performance.json) and
  [CLI result](native2-linux-wheel-performance.txt): Linux 7.0.14 LinuxKit
  aarch64, glibc 2.41, CPython 3.14.8, NumPy 2.5.3. Linux is a Docker Desktop
  guest on the same physical M5 Max, with a local Linux wheel. No bare-metal,
  x86-64 or manylinux performance claim follows from this measurement.

The enforced harness writes its evidence before exit code 1 when 100× fails.
Loaded native binary hashes match actual wheel members. Both platform wheels
were built from the same sdist; the release receipt binds Python/stub sources,
the C source and threshold tests to the qualified artifacts.

## Complete current project CPU pipeline

The read-only source audit also found repeated work outside `_remote_index`:
first remote admission constructs and discards an index, the caller constructs
it again, and final assembly validates and snapshots the combined result.

The [retained ownership prototype](project.md) preserves first-response
validation before subsequent responses, handoff through response extensions,
caller budget/geometry checks and final exact result-class allocation. A
trusted immutable join avoids repeated admission and copies. This join is a
disclosed prototype, not a newly shipped public capability: arbitrary arrays
cannot be trusted merely because they are passed in a tuple.

Using the actual installed kernel 2 wheels, the full numeric pipeline measured
**4.56–12.44× on macOS and 5.18–12.24× on Linux** across the eight ordinary
cases. All 100× checks fail. The clock includes every numeric admission and
assembly stage, the response handoff and final cleanup. Network, model
inference, callbacks, offload scheduling, breaker state and logging are outside
this CPU claim. Removing those policy costs only from the candidate would not
be a valid complete-workflow comparison.

The [macOS](native2-project-macos.json) and
[Linux](native2-project-linux.json) raw samples retain nine alternating 25 ms
rounds, nine valid output oracles, 33 malformed/error-order cases, and observed
native dispatch. Error-only legacy validation preserves existing exception
messages and is disclosed. Both runs pin the same private source capture and
the same installed-source [probe](native2_project_probe.py.txt). All application
definitions remain in the ignored private capture.

The recipe's maximum-capacity case, **2,048 documents × 512 rows**, is measured
separately with the [capacity probe](native2_capacity_probe.py.txt). It checks
every output byte and boundary and observes a fresh finite validation for all
2,048 responses before timing. Its 512 MiB numeric result retains the same
contiguous immutable representation. Raw evidence for
[macOS](native2-capacity-macos.json) and [Linux](native2-capacity-linux.json)
records the results and explicit target verdicts.

| Maximum-capacity CPU pipeline | Before → candidate | Median gain | Conservative gain |
| --- | --- | --- | --- |
| macOS | 84.532 → 18.601 ms | 4.54× | 2.69× |
| Linux | 263.862 → 30.206 ms | 8.74× | 5.04× |

These results include the private immutable-ownership prototype described
above. They do not describe a shipped composition API or total inference
latency. Neither maximum-capacity result reaches 100×.

## Validation and replay

- [macOS installed pack tests](native2-macos-installed-tests.txt): 1,259 passed.
  [Source/API/package/workflow/benchmark gates](native2-macos-tests.txt):
  1,683 passed.
- [Linux installed wheel](native2-linux-wheel.json): 1,259 passed, plus pristine
  NumPy-free and NumPy ownership checks. [Build/test log](native2-linux-wheel.txt)
  retains actual imports and commands.
- [Linux optimized build](native2-linux-optimized.txt) and
  [ASan/UBSan build](native2-linux-sanitizers.txt): all 1,259 pack tests passed
  in each; no sanitizer diagnostics. [Receipt](native2-linux-receipt.json)
  records options and origins.
- With NumPy blocked, [macOS](native2-macos-base-tests.txt) and
  [Linux](native2-linux-base-tests.txt) each passed 547 tests, with 712
  NumPy-dependent cases skipped.
- [Python 3.12/3.13 floor proof](native2-python-floor-checks.json): 11,435
  native/stdlib checks passed per interpreter, with warnings treated as errors
  and NumPy absent. [Program](native2-python-floor-smoke.py.txt) and
  [output](native2-python-floor-checks.txt) are retained.
- [Artifact gates](native2-packaging-tests.txt): 17 passed for direct macOS
  wheel and offline sdist rebuild routes, including native/base execution,
  isolated NumPy, strict types and optional compiler failure. Ruff check/format
  and [strict mypy](native2-static-checks.txt) pass.
- Twelve new threshold regressions bracket 16,383/16,384/16,385 values with
  unaligned storage, both byte orders, finite extrema, subnormals, signed zeros
  and signed quiet/signaling NaNs and infinities.

Replay the public complete-frame harness as documented in the
[main report](../README.md#replay), selecting the installed wheel's
`site-packages` root explicitly. For the broader workflow, copy the retained
probe to `.local/pack-project-native2-replay.py`, supply an authorized Consumer
checkout, and pass `--source` explicitly. The capacity driver imports that same
probe. Frozen source and artifact/evidence hashes are retained in
[`native2-source.tar.gz`](native2-source.tar.gz) and
[`native2-receipt.json`](native2-receipt.json); private application captures are
excluded. Timings establish the reported observations, not a universal speedup
guarantee or an impossibility theorem.
