# Pack backend qualification

This page retains the initial typed-buffer packing qualification. Later work is
recorded separately:

- [Buffer-window APIs and captured consumer qualification](consumer-api/README.md)
  add byte offsets and element counts to the existing decoders.
- [Complete validated numeric-frame decoding](ragged/README.md) adds the generic
  ragged decoder and records the explicit 100× complete-decoder target.

The initial phase made `pack_array_portable` and the no-NumPy `pack_array`
fallback copy matching primitive array storage without first creating Python
scalar objects. That phase changed no public API, dependency, wire format, or
application code; the later API additions are covered by the reports above.

The 100× target is demonstrated for large matching typed-array encoding on both
measured operating systems. It was **not achieved for the captured Consumer
packing operations**: their compiled `Struct` operations and
float32-to-owned-list conversion
remain approximately unchanged. These two results must not be combined into an
application-wide speedup claim.

## Evidence and workload selection

[Consumer capture](consumer-capture.json) records read-only Consumer source hashes,
commit identity, and locations behind the workload selection. The observed
operations include network-endian 256-double embedding records, batches of 200
records, little-endian float32 bytes converted to owned Python floats, and
four-byte HTTP frame lengths. The benchmark measures complete numeric
operations with those shapes and output types; it does not time whole requests,
normalization, network traffic, or durable storage.

The 51,200-element typed-array cases model 200 × 256 values held in a flat typed
buffer. They are a bulk backend opportunity. Consumer currently uses `Struct` with
Python lists at the captured embedding call sites; its other typed-buffer paths
already use NumPy directly. Setup and changing data representation are excluded
from operation timings. Callers starting with lists must include that conversion
in any application comparison.

The [harness](../../profiles/pack_backend.py) runs 32 cases in separate workers
selected before product import. Nine rounds alternate baseline/candidate order.
Each operation is checked against independently constructed stdlib `struct`
bytes or owned values before and after timing. Timings include output
allocation and reference-count cleanup, with cyclic GC disabled during batches.
Raw evidence retains per-round wall/CPU timing, iteration counts, signatures,
source hashes, and separate fresh-process import checks. It rejects changed
sources and wrong imports. Returned bytes are freshly produced; results are not
cached. Both full sweeps and longer focused follow-ups are retained.

## Full comparison

The table uses median complete-call wall time from the 30 ms target batches.
Within each operating system, both sources use the same interpreter and NumPy.

| Portable encode, 51,200 values, matching byte order | macOS before → after | macOS speedup | Linux before → after | Linux speedup |
| --- | --- | --- | --- | --- |
| ndarray `<f8` | 964.585 → 6.230 µs | 154.8× | 1139.903 → 9.229 µs | 123.5× |
| ndarray `>f8` | 949.399 → 5.752 µs | 165.0× | 1231.120 → 9.265 µs | 132.9× |
| ndarray `<i4` | 1107.829 → 3.444 µs | 321.7× | 1280.383 → 4.931 µs | 259.7× |
| ndarray `>i4` | 1041.014 → 3.388 µs | 307.3× | 1347.631 → 5.162 µs | 261.1× |
| stdlib `array('d')`, native order | 681.396 → 5.429 µs | 125.5× | 840.067 → 10.508 µs | 79.9× |

The `<i4` case also exceeds 100× using the fastest baseline round divided by the
slowest candidate round: **150.5× on macOS and 189.9× on Linux**. That is an
observed bound across these samples, not a guarantee for arbitrary machines or
future runs. Scheduler/frequency variation is visible in the raw controls;
some float64 worst-observed bounds fall below 100× despite their median gains.

Small 256-element typed arrays improve 13.1–16.8× in matching byte order.
Byte-swapping the large typed arrays improves 28.5–79.8×; the extra copies and
swap pass remain material. Float16/float32, cross-type conversion, ordinary
lists, generators, and custom iterators retain scalar struct conversion. The
existing NumPy `pack_array` and zero-copy `unpack_array` controls remain near
their original performance.

Ineligible iterables incur a small dispatch cost. The focused Linux generator
case measured 5.922 → 6.497 µs (0.91×); no speedup or regression-free claim is
made for ordinary generators. Matching native stdlib double arrays also remain
below 100× on Linux, as the table shows.

| Captured operation | macOS speedup | Linux speedup |
| --- | --- | --- |
| `Struct('!256d').pack(*list)` | 1.03× | 1.05× |
| Decode `!256d` to an owned list | 1.03× | 1.00× |
| Pack/join 200 embedding records | 0.98× | 1.05× |
| Decode 200 records to owned lists | 1.02× | 1.08× |
| Decode `<f4`, then `[float(x) for x in values]` | 0.97× | 1.03× |

Those compiled scalar aliases already execute in stdlib C; the modified
portable helper is not on their path. The array-view result also cannot replace
an owned-list result in a like-for-like comparison. These measurements leave
the requested 100× improvement to current Consumer workflows unmet.

## Implementation and alternatives

The previous portable encoder snapshots `tuple(seq)` and calls a compiled
repeat-count `Struct.pack`. NumPy scalars therefore require allocation and
conversion even when the input already has exactly the target representation.
The [buffer admission module](../../../src/unstd/pack/buffers.py) admits
exact, one-dimensional `array.array` and already-loaded `numpy.ndarray` inputs.
Matching integer signedness/width and float64 can use their byte storage;
opposite byte order uses stdlib `array.byteswap`. One-dimensional ndarray
strides follow logical iteration order. The helper releases buffer exports and
always returns owned bytes.

Float16/float32 retain `struct`: converting a signaling NaN through a Python
float can quiet or canonicalize it, so raw copies would change wire bytes.
Subclasses, numeric hooks, other shapes/types, and unsupported buffer formats
also retain the existing path. No optional backend is imported by the shortcut.

Evaluation retained NumPy for its existing general coercion and zero-copy
surface. Stdlib `array.frombytes().tolist()` and cached unpack formats offered
only modest improvements for owned-list decoding, not 100×. A hand-written
numeric conversion kernel cannot remove the caller's required Python list or
its argument expansion; this change avoids adding a compiled backend for a job
already handled by primitive buffer copies. Lazy NumPy loading was evaluated
separately: locally it could remove most of a roughly 30 ms scalar import, but
it did not support a 100× import claim and would require resolving existing
runtime annotation/backend-introspection behavior. It was not included.

## Platforms, validation, and reproduction

- [macOS full evidence](macos-py314.json): macOS 26.6 arm64, CPython 3.14.9,
  NumPy 2.5.3.
- [Linux full evidence](linux-py314.json): Linux 7.0.14 aarch64/glibc 2.41,
  CPython 3.14.8, NumPy 2.5.3, in a dedicated Docker Desktop container on the
  same Mac. This exercises the Linux kernel/runtime; it is not bare-metal
  Linux, an x86-64 measurement, or a second physical machine.
- [macOS focused evidence](macos-focused-py314.json) and
  [Linux focused evidence](linux-focused-py314.json) repeat the large matching
  array cases and relevant controls with 100 ms target batches. The full
  sweep remains the primary table above.
- [macOS tests](macos-tests.txt): 1,106 passed, including public API contracts.
  [Linux tests](linux-tests.txt): 1,048 passed across pack and harness contracts.
  [Linux pack public API checks](linux-public-api-tests.txt): two passed.
- With NumPy imports blocked, [macOS](macos-base-tests.txt) and
  [Linux](linux-base-tests.txt) each pass 350 tests, with 689 NumPy-dependent
  cases skipped. The stdlib typed-array shortcut remains active.
  The [captured launcher](base-test-runner.py.txt) makes the import block explicit.

The product tests add 570 differential cases covering integer boundaries,
native/foreign order, strides, signaling NaNs, subclass iteration, conversion
errors, unsupported datetime/timedelta buffer formats, empty arrays, owned
snapshots, and released exports. Nine harness tests reject incorrect outputs,
wrong ownership, wrong sources, and missing evidence inputs. Ruff check/format,
strict mypy for the changed library/harness, and the public signature gate pass.

Extract both source snapshots to replay this initial comparison on a platform
with NumPy installed. Use the retained candidate rather than the current
package, whose later API additions intentionally change its signatures:

```console
mkdir -p /tmp/pack-before /tmp/pack-after
tar -xzf bench/reports/pack/before-source.tar.gz -C /tmp/pack-before
tar -xzf bench/reports/pack/candidate-source.tar.gz -C /tmp/pack-after
python bench/profiles/pack_backend.py --compare-source /tmp/pack-before/src --source /tmp/pack-after/src --rounds 9 --batch-ms 30 --output /tmp/pack-results.json
python -m pytest tests/suites/product/pack tests/suites/assurance/gates/bench/test_pack_backend.py -q
```

[Receipt](receipt.json) records hashes of the frozen source archives, evidence,
and harness. The raw runs retain the exact Python source hashes measured on
each platform. No Consumer source files were changed.
