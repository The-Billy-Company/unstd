# Linux math validation

The final [141-case report](workflows.json) verifies every output and caller
input, with no persistent material regression against equivalent-precision
NumPy or the retained initial package. The [confirmation receipt](confirmation.json)
checks that its Python, C and benchmark sources match the macOS run.

The worker is a local Docker Desktop Linux ARM64 virtual machine on the same
Apple Silicon host as the macOS run. Linux executes native aarch64 instructions;
these are not x86 emulation measurements or bare-metal server measurements.

The repository is mounted read-only. A private copy under `/tmp/math-run`
contains the package, benchmark and built Linux extension; build outputs never
replace the host's macOS extension. The container is limited to one CPU and
2 GiB, pinned to vCPU 9, with OpenBLAS, OMP and MKL thread counts set to one.

[`environment.json`](environment.json) records the interpreter and dependency
versions. Both platforms use NumPy 2.5.3. This Linux interpreter is CPython
3.14.4; the macOS interpreter is 3.14.9. Differences in their NumPy builds,
compilers, BLAS providers and operating systems are retained in benchmark
provenance instead of being treated as identical environments.

## Build and correctness

The extension is compiled from `native/math/src/adapter.c` through setuptools
with `-O3 -fno-fast-math -ffp-contract=off -Wall -Wextra -Werror`, linked with
the platform math library. It uses Python's buffer protocol and no NumPy headers.
The compiler command is retained in [`native-build.txt`](native-build.txt).

The separate undefined-behavior sanitizer build uses
`-fsanitize=undefined -fno-sanitize-recover=undefined`. Its compiler output and
test output are retained in [`ubsan-build.txt`](ubsan-build.txt) and
[`ubsan-tests.txt`](ubsan-tests.txt). Sanitizer binaries are not timed.

The release extension and sanitizer extension each pass **172 tests**. The
fallback passes **85 tests**, with 87 tests that specifically require the
extension skipped. [`artifact-manifest.json`](artifact-manifest.json) records
the exact product and binary hashes for both builds.

## Measurement

Run the standalone benchmark script from the copied project root, with its
`src` directory on `PYTHONPATH`:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python bench/profiles/math_workflows.py --rounds 9 --strict --require-native \
  --output /evidence/workflows.json
```

This route requires only the math dependencies; importing the broad `bench`
package with `python -m` also loads unrelated optional benchmark backends.
The workflow report includes NumPy, the retained initial implementation and
the candidate, matching calculation precision across the compared lanes.
Every output is checked, and raw timing samples and source/binary hashes are
retained. The documented regression tolerance is a measurement guard, not a
promise of zero overhead for arbitrary inputs or a deployment-latency claim.

The unchanged guard is `candidate <= reference * 1.15 + 300 ns`. Every guard
passed in the final nine-round run, with no confirmation reruns needed. The
profile allows at most two 21-round confirmations for initially flagged rows;
the final report records this policy and its empty initial/recheck sets.
Earlier captures retain flagged measurements and their confirmations, rather
than replacing that history with the final result.

Some rows remain near parity rather than accelerating. In particular, the
strided float64 mean-square result is about equal to NumPy and 0.901×
the initial helper's speed, within the stated guard. The final Linux run does
not establish literal zero overhead on every input.

Complete workflow gains against NumPy range from 1.38–2.60× for retrieval,
1.78–8.89× for framing plus dB energy, and 1.06–1.46× for list pooling.
Clustering ranges from 0.99–1.27×, with its larger shape near parity.

Exploratory reports retain the initial native-loop losses and crossover tests.
They explain why bulk rows use NumPy while small rows use the native kernels;
they are not the final platform certificate.
