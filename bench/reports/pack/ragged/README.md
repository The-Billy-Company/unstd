# Complete numeric decoder qualification

**The requested complete-decoder 100× target is not met on either measured
operating system.** The new generic `unstd.pack.ragged.RaggedDecoder` removes
duplicate scans and payload copies, but every floating value still requires
fresh finite-value validation. No Consumer application files were changed.

The current shipped validator is now **kernel version 2**. The
[continued qualification](alternatives/native2.md) records its medium-frame
improvement, installed-wheel results on both systems, broader current project
and maximum-capacity replays, and the additional backend experiments. It still
fails the 100× target. The original version-1 measurements below are retained
as historical evidence.

## Initial version-1 measured result

The table uses the installed release wheels. These are complete calls,
including numeric validation, NumPy array creation,
allocation of the exact captured application result class, initialization of
its identity/device state, and reference-count cleanup. Nine rounds alternate
baseline/candidate order with 50 ms target batches. Setup and output oracles
are outside timing; cyclic GC is disabled during batches.

| Float32 matrix, 128 columns | macOS before → after | Median gain | Observed conservative gain | Linux before → after | Median gain | Observed conservative gain |
| --- | --- | --- | --- | --- | --- | --- |
| 128 rows, 64 KiB + 16-byte table | 6.336 → 1.722 µs | 3.68× | 2.89× | 7.166 → 1.872 µs | 3.83× | 2.30× |
| 8,192 rows, 4 MiB + table | 172.839 → 47.424 µs | 3.64× | 2.04× | 1,579.952 → 51.286 µs | 30.81× | 19.07× |
| 65,536 rows, 32 MiB + table | 1,523.688 → 388.357 µs | 3.92× | 3.53× | 3,551.158 → 423.182 µs | 8.39× | 6.86× |

The conservative gain is the fastest baseline sample divided by the slowest
candidate sample. All six cases fail both the median and conservative 100×
checks. The harness writes full evidence before returning exit code 1 for an
enforced target failure. These are observed sample bounds, not guarantees for
other hardware or future runs. The Linux baseline has substantially higher
allocation/copy cost at 4 MiB; its speedup must not be substituted for macOS.

- [macOS installed-wheel evidence](macos-wheel-py314.json): macOS 26.6 arm64, CPython 3.14.9,
  NumPy 2.5.3, native NEON validator.
- [Linux installed-wheel evidence](linux-wheel-py314.json): Linux 7.0.14 LinuxKit aarch64,
  glibc 2.41, CPython 3.14.8, NumPy 2.5.3, native NEON validator. Linux ran in
  a dedicated Docker Desktop container on the same physical Mac. This is an
  actual Linux runtime measurement, not independent bare-metal or x86-64 proof.
- Earlier frozen-source runs are retained for [macOS](macos-py314.json) and
  [Linux](linux-py314.json). They measured the same Python and C source, with
  separately compiled binaries, and also failed every 100× target. Installed
  binary hashes match the wheel receipts linked below.

At 4 MiB, 100× would require a complete macOS call below 1.728 µs. The early
[diagnostic prototype](mac-initial-probe.json) took 47.029 µs for its scan alone,
compared with 55.340 µs for NumPy's finite check. That prototype used a simpler
result wrapper and is **not** the complete-decoder evidence above. Its scan
result explains the remaining bottleneck empirically; it is not a proof that
every possible algorithm is bounded by that timing. Cached validity, skipped
validation, and leaf-only view timings do not establish this target.

## Generic implementation and caller adoption

The [public layout](../../../../src/unstd/pack/ragged.py) is configured with
numeric value/index dtypes, column count, finite-value policy, and whether empty
segments are allowed. Each `unpack` supplies the boundary count. The module
validates complete rows, a zero first boundary, ordered in-range boundaries,
the final row count, and optional finiteness. It contains no model dimensions,
identities, publication allowances, or application result classes.

The [native validator](../../../../native/pack/README.md) uses checked integer
loads and IEEE exponent-bit classification. NEON and SSE2 scan float32/64;
other cases use scalar integer bits. Independent SIMD reductions avoid a
dependency-bound scan. Large scans release the GIL in bounded chunks and check
signals. There is no NumPy C ABI dependency, validation cache, or result cache.
The stdlib fallback enforces the same admission contract.

Exact immutable bytes back read-only NumPy views without payload copies. All
other C-contiguous exporters are snapshotted before validation, including
read-only views of mutable storage. Without NumPy, the result is immutable tuple
rows and integer boundaries. Views retain the entire frame; callers needing
independent array storage must include their copies in their own measurement.
This follows the ownership distinction in NumPy's
[`frombuffer` documentation](https://numpy.org/doc/stable/reference/generated/numpy.frombuffer.html).

The [qualification adapter](../../../profiles/ragged.py) retains the captured
caller's geometry/publication guards and identity check. It replaces numeric
admission and snapshots with the configured generic decoder, then constructs
the **same** captured frozen result class and sets `_device=None`. It requires
the reviewed admission predicate and snapshot statements exactly; source drift
fails qualification. Application adoption needs a validated construction path
that avoids repeating the old constructor's scans and copies. Simply passing
these arrays through that unchanged constructor repeats the work and does not
produce the table's gains.

The baseline comes from read-only captures of `TokenIndex` and `_remote_index`
in Consumer's ColBERT module. [Provenance](../consumer-api/consumer-provenance.json)
pins the source, definitions, model identity, and private capture hash. Synthetic
finite payloads follow that wire geometry. Transport, inference, normalization,
and the rest of Consumer are outside this decoder measurement. Application source
definitions remain in the ignored private capture, outside release artifacts.

Output oracles check every value, exact dtypes/shapes, result class, byte counts,
device state, immutable owners, and expected sharing/copying. Ten adversarial
cases preserve caller exception messages, including late NaN/positive/negative
infinity. Untimed native observation proves dispatch on repeated bytes and on a
late invalid value. Source and binary fingerprints must remain unchanged across
measurement.

## Contracts and release coverage

- [macOS contracts](macos-tests.txt): 1,671 passed across pack, API/package,
  source inventory, workflow, benchmark, and release gates.
- [Linux contracts](linux-tests.txt): 1,276 passed across pack and all three
  pack benchmark harnesses.
- With NumPy imports blocked, [macOS](macos-base-tests.txt) and
  [Linux](linux-base-tests.txt) each passed 535 contracts, with 712
  NumPy-dependent cases skipped.
- [Linux ASan/UBSan](linux-sanitizers.txt): 121 public/native decoder contracts
  passed, with allocator redzones enabled and no sanitizer diagnostics.
- [macOS Python floor checks](python-floor-checks.json): Python 3.12.15 and
  3.13.17 each built the native kernel with warnings treated as errors and passed
  11,435 functional checks. Native and forced-stdlib decoding run under `-I -S`
  without NumPy; dtype/order combinations, boundaries, nonfinite values,
  snapshots, metadata guards, and annotation introspection are covered. The
  [smoke program](python-floor-smoke.py.txt) and
  [commands/output](python-floor-checks.txt) are retained. These are compatibility
  checks, not additional performance measurements.
- [Installed artifacts](packaging-tests.txt): 17 passed for direct macOS wheels
  and wheels rebuilt offline from the sdist, including source completeness,
  base native/tuple execution, the isolated NumPy extra, strict downstream types,
  and an independent optional pack-compiler failure. The
  [artifact receipt](macos-artifact-receipt.json) matches measured Python/stub
  sources against both wheels and the native C source against the sdist.
- [Installed Linux wheel](linux-wheel-tests.json): the same sdist rebuilt to a
  Linux aarch64 wheel. A pristine consumer passed base native/tuple checks, then
  NumPy ownership/admission checks and all 1,247 pack tests. Product origins were
  checked before and after pytest; no workspace product code was imported.
  [Build and test output](linux-wheel-tests.txt) retains compiler and install
  commands. This local wheel was not auditwheel-repaired or published.
- [Static checks](static-checks.txt): strict mypy passes for the package and
  complete-decoder harness. Ruff check and formatting pass for the package,
  new tests, and harness.
- [Benchmark registry checks](benchmark-registry-tests.txt): 120 passed,
  including public operation coverage, frozen baseline completeness, repeated
  independent workload oracles, and comparison workflow policy. Constructor and
  unpack workloads are registered separately from this captured consumer run.

The native build registry, sdist source requirements, release verifier, wheel
runtime import checks, installed wheel test partitions, and native/sanitizer CI
jobs include pack. The compiler remains optional outside strict release builds;
the base installation has no new runtime dependency.

## Replay

Regenerate the private source capture from an authorized local Consumer checkout:

```console
python bench/profiles/pack_consumers.py --consumer-root ../consumer --write-capture .local/pack-consumer-capture.json
```

Build/install the current unstd wheel with a C compiler and NumPy, then select
its installation root (the directory containing `unstd`) explicitly:

```console
python bench/profiles/ragged.py --capture .local/pack-consumer-capture.json --source /absolute/installed/site-packages --rows 128 8192 65536 --rounds 9 --batch-ms 50 --require-native --target-speedup 100 --enforce-target --output /tmp/ragged-results.json
python -m pytest tests/suites/product/pack tests/suites/assurance/gates/bench/test_ragged_profile.py -q
```

The measured source and qualification inputs are retained in
[`source.tar.gz`](source.tar.gz); [`receipt.json`](receipt.json) records hashes
for the archive, native sources, public API, harness, raw evidence, and checks.
This focused archive is not a complete release checkout. To replay its pack
source directly, extract it and build the validator with the selected Python:

```python
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext

extension = Extension(
    "unstd.pack._pack_native",
    ["native/pack/src/adapter.c"],
    extra_compile_args=["-O3"],
)
build = build_ext(Distribution({"name": "pack-replay", "ext_modules": [extension]}))
build.build_lib = "src"
build.build_temp = "/tmp/unstd-pack-replay-build"
build.ensure_finalized()
build.run()
```

Then use `--source src` in the harness command. The private capture must be
supplied separately. The earlier
[buffer encoding](../README.md) and [range API](../consumer-api/README.md)
measurements remain separate; their leaf speedups are not complete decoder gains.
