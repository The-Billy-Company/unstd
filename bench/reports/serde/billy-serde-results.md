# Serde regression investigation

Historical measurements for the first 3.1.0 candidate. See the
subsequent optimization report for the
current implementation and its stricter comparison with 2.1.1.

Measured 2026-10-19 on macOS arm64, Python 3.14.6, orjson 3.13.0,
msgspec 0.22.0 and pybase64 1.5.0. The patch keeps the consumer public calls intact.
It recovers most of the JSON performance lost in 3.0.0 while retaining exact
stdlib output. It does **not** establish zero slowdown against 2.1.1.
Compared with 2.1.1, compact encoding remains 9.6% slower, NDJSON writing 6.9%
slower, NDJSON reading 22.4% slower, and tiny request encoding costs 57 ns more.

## What changed

The compact source fixture contains 103 containers/strings and no numbers.
The old 16-value Python snapshot rejected it, so its compact byte encoder used
stdlib. The optional C guard now checks eligible builtin trees and invokes its
trusted orjson encoder within one uninterrupted GIL span. It avoids a Python
snapshot without allowing mutation or user callbacks between validation and
encoding. Missing compilers and unsupported interpreters keep the portable
implementation.

The decoder uses a reusable msgspec decoder with arbitrary-precision integers
and correctly rounded floats for immutable input. Stdlib owns failed inputs,
custom behavior, and recursion diagnostics.
NDJSON inherits these codecs through its existing JSON seam.

## Compared with 3.0.0

Each row is from the same before/after run; times are microseconds per complete
public call. The final run started after the full test suite finished.

| Saved consumer operation | 3.0.0 | Patch | Before / after |
| --- | ---: | ---: | ---: |
| Compact source encoding | 35.59 | 2.23 | 15.97× |
| NDJSON writing | 35.66 | 6.56 | 5.43× |
| NDJSON reading | 28.17 | 11.71 | 2.41× |
| Small request encoding | 0.590 | 0.163 | 3.61× |
| Small request decoding | 0.508 | 0.139 | 3.66× |
| Source decoding | 8.65 | 5.66 | 1.53× |

All 16 current workloads matched their independent references and required
native probes. The explicit `--max-regression 1.15` gate passed. That allowance
is a local regression control, not a claim that every input is faster.

## Compared with 2.1.1

This was a separate alternating run with identical source fingerprints. Its
absolute costs must not be mixed with the preceding run.

| Saved consumer operation | 2.1.1 | Patch | Patch change |
| --- | ---: | ---: | ---: |
| Compact source encoding | 2.06 | 2.26 | 9.6% slower |
| NDJSON writing | 6.56 | 7.01 | 6.9% slower |
| NDJSON reading | 9.82 | 12.02 | 22.4% slower |
| Small request encoding | 0.126 | 0.183 | 45.6% slower; 57 ns |
| Small request decoding | 0.139 | 0.151 | 9.0% slower; 13 ns |
| Source decoding | 5.15 | 5.86 | 13.9% slower |
| URL Base64 image decoding | 69.48 | 39.70 | 42.9% faster |

The large 3.0.0 regressions are gone, but exact compatibility still costs more
than the former permissive native path in these JSON cases. Small changes on a
shared laptop remain sensitive to clock and scheduling state. No strict
zero-regression gate against 2.1.1 is asserted.

The default source JSON changed bytes between 2.1.1 and the stdlib-compatible
implementation, although parsed values agree. It receives no release ratio.
The wire Base64 APIs and typed NDJSON reader did not exist in 2.1.1; their
independent-reference comparisons remain valid, but no old-release ratio is
invented.

## Evidence and reproduction

The serde fixture profiler in the [profile directory](../../profiles/) imports the existing saved-fixture benchmark.
The consumer benchmark continues to own workload construction, independent references, CPU batch
timing and output checks. Workers use one interpreter and alternate source order
across three repetitions, each containing nine alternating actual/reference
CPU batches. Reports retain every sample, backend versions, fixture hashes,
package source hashes including C/stubs/binaries, and the original benchmark
hash. A source or fixture change during measurement rejects the report.

The profiler honestly records entry into `_json_native.encode`; it does not
invent a nested orjson profiling event. An additional untimed probe verifies
that the required request fixture is accepted by the guard and matches its
independent reference. Neither guard entry alone nor a timing ratio substitutes
for the native guard's parity tests.

The original benchmark names orjson as its required JSON decoder. This
runner recognizes the new msgspec decoder and native encoding guard without
changing product calls. The original script's backend-specific probes need the
same adjustment before they can gate an upgraded installation directly.

The raw reports in this directory retain the final patch comparison with 3.0.0,
the final patch comparison with 2.1.1, and the original 2.1.1 to 3.0.0 regression
reproduction.

Worker distribution metadata describes installed packages. The selected source
paths and SHA-256 manifests identify the code actually measured; extracting a
Git tag does not install that tag's distribution metadata.

```console
uv sync --all-extras --group dev
```

Editable installation builds the optional native guard in place. Use a valid
compiler and SDK to reproduce native timings. On this host, selecting the
installed macOS 26 SDK avoided an incompatibility between the default macOS 27
SDK and installed linker; the build hook contains no machine-specific SDK path.

## Validation

The complete repository suite passed with 5,946 tests, 38 skips and 250 explicitly
deselected tests. After the final C optimization, the installed ABI3 wheel's
complete serde suite passed on Python 3.14 with 2,622 tests and 21 skips.
The final native wheel passed `abi3audit --strict`, and pristine installed base
and serde-extra consumer probes passed.

Artifact tests verify source/data preservation, complete wheel RECORD hashes,
ABI3 versus portable metadata, source-distribution rebuilds, and compiler-less
fallback. Editable binary replacement uses a fresh inode to preserve already
loaded macOS mappings; failed editable builds clear their own stale native
artifact.

The release workflow prepares 64-bit Linux, macOS and Windows wheels, with
separate Intel/ARM runners, installed serde tests and a source distribution.
Only macOS ARM native artifacts were built and audited
locally. These measurements precede the 3.1.0 release.
