# Math edge cases and complete workflows

We narrowed the native path to workloads where it helps. Small ndarray calls,
grouped accumulation and frame construction use portable C buffer kernels.
Large reductions keep NumPy's vectorized implementation, and gapped column
views take a layout-specific path. Dense top-k requests use a full stable sort.
For gapped views a contiguous copy or squared temporary can beat a strided
contraction; the implementation chooses throughput over a universal no-copy claim.

The comparison includes 141 workloads on macOS and Linux. It checks the entire
public call against both equivalent-precision NumPy expressions and the
[initial implementation](edge-baseline.py). Every result and input remains
verified outside the timed sections. Raw slower rows remain in the evidence.

Both final runs pass all 141 guards with **zero initial material regression
flags**, so neither final run needed a confirmation. The reports match the
final Python, C and benchmark sources. Earlier losing exploratory runs remain
under the Linux evidence directory.

| Workload | macOS speedup over NumPy | Linux speedup over NumPy |
| --- | ---: | ---: |
| Stable top-k, 64 float32 scores, k=16 | 1.64× | 2.59× |
| Normalize 8×64 float32 rows | 1.71× | 2.07× |
| Cosines against 8×64 float32 rows | 2.52× | 2.71× |
| Four group means over 2,000×16 float32 rows | 3.91× | 4.78× |
| Complete retrieval over 4,096×128 float32 embeddings | 4.36× | 2.60× |
| Clustering 128×64 float32 embeddings | 0.96× | 1.00× |
| Complete framing through dB energy, 160,000 float32 samples | 10.00× | 8.89× |
| Complete weighted pooling from 64×64 Python lists | 1.07× | 1.06× |

The clustering row is approximately parity, not an acceleration. Several raw
rows remain slightly slower than a reference, including Linux float64 gapped
mean-square versus the old helper. The claim is no measured **material**
regressions under the declared guard, not zero raw latency losses.

## Reproduce

```sh
python -m pytest tests/suites/product/math -q
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
  python -m bench.profiles.math_workflows --require-native --strict --rounds 9 \
  --output /tmp/math-workflows.json
```

The [benchmark](../../profiles/math_workflows.py) uses nine rotated timing
rounds, calibrating each lane to roughly five milliseconds. A material regression
means candidate median latency exceeds a reference by **15% plus 300 ns**. Both
NumPy and the previous implementation must pass independently. This is a bounded
measured claim; it does not promise literally identical latency on every call or
every machine.

If an initial case is flagged, it receives up to two 21-round confirmations
under the same criteria. Only failures against the same reference persisting
through every attempt fail the gate. Initial measurements and every confirmation
remain in the report; the harness never selects the fastest round or discards
an inconvenient sample. Six harness tests verify the confirmation logic.

## Coverage and correctness

All eight operations have direct workloads. Cases include empty/singleton inputs,
top-k fractions through full sorting, float32/float64, C/Fortran/gapped layouts,
small and many groups, lists, and complete retrieval, clustering, weighted pooling
and frame-to-dB workflows. Calculation precision is matched. Default float64
accuracy is not compared against float32 arithmetic as an equal-work speed claim.

The 172 product tests include direct native admission and fallback checks,
integer ordering, malformed inputs, nonmutation, read-only and strided buffers,
empty shapes, overflow and underflow. Nonempty frame views retain their source
and a read-only ownership barrier: callers cannot make them writable again.
Exceptional native arithmetic defers to NumPy so `errstate` raises, warnings and
callbacks retain their behavior. The Linux build also runs the suite under UBSan.

## Platforms and artifacts

The macOS run uses native ARM64 CPython 3.14.9. The Linux run uses native ARM64
CPython 3.14.4 in a local Docker Desktop Linux VM, with no instruction emulation.
Both use NumPy 2.5.3 and one BLAS thread. Linux VM measurements demonstrate that
platform; they are not bare-metal or x86 performance measurements.

- [macOS raw results](edges-macos.json)
- [Linux environment, tests and raw results](linux/README.md)
- [Original measurements](README.md), retained as historical evidence

Each report records Python, NumPy, BLAS configuration, native binary and C source
hashes, the exact initial reference, all raw samples and traced allocations.
Cold first public-call latency after imports is separate from steady-state
timing. Import/setup cost is not included in the throughput claim.

The performance workflow requires the installed native kernel and runs this
strict comparison on macOS and Linux with Python 3.13 and 3.14. Release and
sanitizer jobs build and exercise the math extension. The base package still
imports without NumPy, and unsupported inputs or unavailable compilers retain
the portable path. The portable path has no universal speed guarantee.
