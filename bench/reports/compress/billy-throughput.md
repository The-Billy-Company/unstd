# Gzip throughput qualification

Paired measurements on 2026-10-25 compare the previous native gzip implementation
with this change on the actual Mac and Foundry CPU. Four independent
upload streams gained **1.83× on Mac and 4.58× on Linux**. Linux bounded HTTP
expansion refusal gained **13.24×**; its two-pass dataset uploads gained about
**1.26×** in the single-stream consumer measurements. Mac single-stream large
upload performance remained approximately unchanged. These are measured workload
results, not an equal-runtime guarantee across different hardware.

## What changed

The public Linux reader selects zlib-ng's SIMD inflater. macOS retains its faster
platform libz, with a per-decoder lock and GIL release during native calls with
at least 32 KiB output space. Both reuse the running interpreter's gzip read
implementation, framing, buffering, CRC/trailer checks, member handling and
error-delivery order. Native libdeflate CRC32 remains active. Writers retain
zlib-ng. Oversized flush modes now fail without silently truncating to a valid
native mode or finishing the stream.

Public reads retain incremental output budgets and existing `readinto` staging.
There is no decoded-output cache across rewind, speculative whole-member stream
decode, shared application context, or replacement of interpreter framing. The
private `decompress_into` primitive is tested but is not used by public reads;
its isolated copy savings are not included as an application speed claim.

## Machines and method

| Property | macOS | Linux |
| --- | --- | --- |
| CPU | Apple M5 Max, 18 logical CPUs | Foundry AX102, Ryzen 9 7950X3D, 16 cores / 32 threads |
| Interpreter | CPython 3.14.8, ordinary GIL build | CPython 3.14.8, ordinary GIL build |
| Extension toolchain | Apple Clang 21, release `-O3` | GCC 12.2, release `-O3` in the local CI runner container |
| Runtime | macOS 26.6 arm64 | Linux 6.8 x86_64; container glibc 2.36 |
| Active streaming codec | Apple libz 1.2.12 | zlib-ng 2.2.5, python-zlib-ng 1.0.0 |
| Native CRC | libdeflate 1.25 | libdeflate 1.25 |
| CPU availability | normal scheduler; busy shared laptop | one pinned CPU for consumer latency; four distinct physical cores for concurrency |

Each host measured separate before/current installations with identical
third-party dependency versions, frozen compressed bytes, and consumer functions.
Both controls already used native codecs. Raw reports identify loaded Python and
binary sources, active decoder, native calls, fixture hashes and interpreter.
The Linux bundled adapter source hashes match the Mac build. The qualification
manifest retains compiler settings and Linux CPU selection; no host packages or
production services were changed. Native libarchive was installed only inside a
disposable qualification container for the archive tests.

The 14-case consumer run alternates installations for three fresh-process
repetitions, seven calibrated rounds each. The summary uses the median of the
three minimum batch means, the existing estimator used by the latency harness.
Every result is checked before and after timing. Wall time is the regression
gate; CPU samples are diagnostic. All 14 cases on each host passed the 1.30×
maximum-slowdown gate. Individual small gains/losses include scheduler noise;
unchanged writer controls show that not every difference is an implementation
improvement.

The frozen fixtures reproduce captured gzip consumption shapes: real `ijson`
batching and EOF exhaustion, two 256 KiB upload passes with digest/count and
rewind/position restoration, HTTP `read(max_bytes + 1)`, and per-line journal
writes with batch flushes and one/two-member replay. They use synthetic records,
not private production data. RPC, async scheduling, filesystem latency and
application reconstruction are excluded. Consumer source-symbol hashes are
retained so this is an auditable codec/I/O comparison.

## Independent upload streams

Seven alternating paired processes per worker count use persistent, warmed
executors outside the timer. Every worker opens its own stream and completes
both upload passes. Throughput below is **logical expanded bytes processed per
second, counting both passes**; it is not compressed network throughput.

| Workers | Mac current | Mac speedup | Linux current | Linux speedup |
| --- | --- | --- | --- | --- |
| 1 | 2.037 GB/s | 0.995× | 2.506 GB/s | 1.251× |
| 2 | 3.662 GB/s | 1.161× | 4.996 GB/s | 2.354× |
| 4 | 5.959 GB/s | 1.825× | 9.321 GB/s | 4.579× |

Single-stream Mac performance stays within about 0.5% of the previous native
path in this independent-stream diagnostic. Its benefit grows with independent
streams because native inflation can proceed while other Python threads run.
The Linux four-worker result scales to 3.72× its own one-worker throughput.
These bounded measurements do not establish scaling beyond four workers.

## Individual consumer latency

Speedup is previous/current wall time; greater than 1 is faster.

| Frozen consumer case | Mac speedup | Linux speedup |
| --- | --- | --- |
| `dataset-query-100` | 1.329× | 1.015× |
| `dataset-upload-100` | 1.106× | 1.047× |
| `dataset-query-20001` | 1.036× | 1.011× |
| `dataset-upload-20001` | 1.005× | 1.265× |
| `dataset-query-40001` | 1.050× | 1.013× |
| `dataset-upload-40001` | 0.972× | 1.263× |
| `http-bounded-success` | 0.988× | 1.514× |
| `http-expansion-refusal` | 0.997× | 13.241× |
| `journal-replay-200-1-members` | 1.092× | 1.001× |
| `journal-replay-200-2-members` | 1.072× | 1.000× |
| `journal-write-flush-200` | 1.087× | 0.998× |
| `journal-replay-20000-1-members` | 1.253× | 1.023× |
| `journal-replay-20000-2-members` | 0.998× | 1.008× |
| `journal-write-flush-20000` | 0.960× | 0.987× |

Dataset query cases include substantial parser/application work, so faster
inflation contributes little to their complete consumer latency. The bounded
expansion case is deliberately highly compressible and demonstrates that
particular refusal path; 13.24× is not a general gzip throughput multiplier.

## Correctness and portability

The complete compression product suite passed **699 tests on Mac** and
**698 on Linux**. Linux skipped only the optional mypy consumer check, which
passed on Mac. Separately compiled current gzip C binaries additionally passed
**308 checks on Python 3.12 and 308 on 3.13**, including native threading, reader
state, flush modes and zlibx. The native benchmark/API gates passed **150
checks**, and strict mypy passed for the 26 compression/harness source files.
Installed native wheels and wheels rebuilt from source distributions additionally
passed 155 packaging/consumer checks each on Python 3.12.15 and 3.14.9, including
isolated optional codecs, base fallbacks, native archive calls and typing negative
controls. The latency baseline adds 18 measured zlibx cases while preserving all
246 previous rows and ceilings; full registry coverage validates all 264 cases.
Shared-state probes include bounded competing consumers, reset during inflation,
GIL progress on one allowed CPU, and reentrant error lookup. Buffer ownership,
malformed-member partial delivery, EOF/tails, dictionaries, copy, callbacks,
flush continuation and original-module identity have dedicated contracts.

The [port matrix](port-contracts.md) maps the remaining external codecs and
archives to their exact provider modules. It distinguishes decoder budgets,
application policy and persisted compressed-byte requirements. This does not
qualify a host's native-library patch level or promise identical output/speed
across different provider versions.

## Retained evidence and reproduction

- Mac and Linux consumer timings are retained in this directory.
- [Mac independent-stream timings](concurrency-macos-py314.json)
- [Linux independent-stream timings](concurrency-linux-py314.json)
- [Source, build and test qualification](qualification-py314.json)
- [Python 3.12/3.13 native compilation qualification](native-python-versions.json)
- [Final installed-wheel and source-distribution consumer receipt](packaging-final-receipt.json)
- Test outcomes: [Mac](tests-macos-py314.txt), [Linux](tests-linux-py314.txt), [benchmark/API gates](gates-macos-py314.txt)

The performance workflow repeats the frozen consumer guard on Linux/macOS with
Python 3.13/3.14 and independently built revisions. Concurrency remains an
explicit diagnostic: shared hosted runners cannot provide a stable scaling
ceiling. Actual end-to-end service throughput still depends on the machine,
stream mix, storage, network and scheduler.
