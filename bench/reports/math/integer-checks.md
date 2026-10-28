---
doc_radar:
  sentinels:
    - file: ../../../src/unstd/math/__init__.py
      contains: ['def within(', 'np.array_equal(left, right)', 'carry = negative &']
    - file: ../../profiles/integer_checks.py
      contains: ['def _prove_consumer(', '"consumer_within_calls"', '"source_sha256"']
---

# Exact Integer Checks

This is the first-pass capture. The [second pass](integer-checks-second-pass.md)
retains this implementation and measures the later changes against it.

We compare integer arrays without converting every value into a Python object.
`math.within` preserves full signed/unsigned ranges, including a mixed distance
above uint64. Zero scalar tolerance with equal signedness takes a direct equality
scan; other cases use unsigned distances with an explicit carry bit.

## Measured Calls

The [raw capture](integer-checks-macos.json) retains seven alternating paired
rounds on macOS arm64, CPython 3.14.9 and NumPy 2.5.3. The Consumer column runs
the actual numerical `close` function, including its validation and exception
handling; each untimed route proof counted one real `within` call.

| Integer check | Consumer before | Consumer after | Speedup |
| --- | ---: | ---: | ---: |
| Equal, 8 values | 10.304 µs | 7.205 µs | 1.43× |
| Equal, 2,500 values | 125.604 µs | 8.346 µs | 15.05× |
| Equal, 100,000 values | 5.572 ms | 26.075 µs | 213.67× |
| Equal, 1,000,000 values | 59.830 ms | 239.115 µs | 250.22× |
| Distance three, 8 values | 10.009 µs | 9.884 µs | 1.01× |
| Distance three, 1,000,000 values | 68.299 ms | 1.287 ms | 53.08× |
| Mixed signed/unsigned extrema, 4,096 values | 222.849 µs | 22.628 µs | 9.85× |
| Mixed carry rejected, 4,096 values | 229.385 µs | 12.468 µs | 18.40× |
| Read-only nonnative endian, 4,096 values | 225.252 µs | 23.044 µs | 9.78× |
| Strided, 4,096 values | 219.016 µs | 19.530 µs | 11.21× |

The 100× target is met for bulk equality checks. It is not met for every
comparison: the eight-value primitive with nonzero tolerance regressed to
0.76×, while the complete Consumer call measured 1.01×. Both measurements and
all other fixtures remain in the raw capture.

Peak Python allocations for the million-value equality call fell from
127,973,260 to 1,000,565 bytes in a separate tracemalloc pass. Nonzero tolerance
still allocates unsigned working buffers; its million-value call peaked at
26,001,068 bytes.

## Reproduce

Run the public helper comparison, adding the Consumer's source to measure its
complete check through both backend routes.

```sh
python -m bench.profiles.integer_checks --output /tmp/integer-checks.json
python -m bench.profiles.integer_checks --consumer-source /path/to/numerics.py --output /tmp/consumer-integer-checks.json
```

The profile checks an independent Python-integer oracle before and after timed
batches, proves helper entry separately, and retains source and input hashes.
Source stability is checked across the run; backend availability changes stay
outside timing. Allocation tracing runs separately from timing.

These are synthetic CPU workloads on one shared machine. They exclude input
construction, kernel startup, model and network latency. Relative and floating
tolerances stay on the Consumer's existing numerical path; this capture does
not certify a released package or deployed application.

## Verification

The math, public API and benchmark coverage suites passed 374 tests. Focused
Ruff and strict mypy checks passed; the additive speed row preserves all 304
earlier measurements and ceilings with its own captured environment and
provenance.

The historical global speed baseline uses another Python environment, so its
absolute latency gate cannot run on this worker. Its coverage/correctness gate
passes; no historical ceiling was changed to admit this function.
