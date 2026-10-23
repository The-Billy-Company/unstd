---
doc_radar:
  sentinels:
    - file: src/unstd/compress/_reader.py
      contains: ['_factory = _accelerated.Inflater', '_zlib_globals["crc32"] = _crc32']
    - file: native/compress/src/adapter.c
      contains: ['libdeflate_gzip_decompress_ex(', 'MAX_OUTPUT_BYTES', 'sizeof(ModuleState)']
    - file: bench/gzip_reads.py
      contains: ['bounded-blake3-rewind-stream-v1', '_validate_upload_bytes']
    - file: bench/gzip-native-reads-results.json
      contains: ['"runs": 31', '"require_native": true', '"native_decode_executed": true']
    - file: bench/gzip-native-reads-linux-results.json
      contains: ['"runs": 31', '"require_native": true', '"native_decode_executed": true']
    - file: bench/gzip-joined-upload-diagnostic.json
      contains: ['historical joined-upload negative diagnostic', '473a2b931336005ad65a6e8effe953e886223b6e77e5acac9f18524d80150bcf']
---

# Native gzip read qualification

Version 3.1.4 moves streamed inflation and CRC32 to compiled kernels while
retaining the running interpreter's gzip framing, buffering and error delivery.
Complete eligible byte buffers use libdeflate. Small buffer calls reuse a
module-owned decoder while holding the GIL; large calls use independent decoder
state and release the GIL. Base installs retain exact stdlib bindings.

Both final reports compare installed candidate wheels with published 3.1.3 and
direct stdlib. All 14 cases in each report preserve the independent source bytes
and execute the native decoder; eligible complete buffers also pass explicit
native acceptance checks. Raw and compressed input hashes match across variants
and across the two platforms.

The runner jointly calibrates common fixed iterations to at least 20 ms CPU per
batch, disables GC within timed batches, and alternates variant order for 31
repetitions. Setup, output validation, profiling, process startup and IPC are
excluded. Reports retain CPU and wall samples, source/binary hashes, fixture
hashes, interpreter details, dependency versions and native codec versions.
Speedup below is the 3.1.3 median CPU time divided by the candidate median CPU
time; values above 1 indicate lower candidate CPU cost.

Upload cases follow Billy's bounded consumption: read 256 KiB chunks to compute
an incremental BLAKE3 digest and byte count, rewind, then read/count/discard the
second pass. The timed result contains a 32-byte digest and two counts. Before
timing, a separate validator checks every chunk in both passes against the
original bytes. Output SHA256 and byte evidence remain in the report. Both
environments use BLAKE3 1.0.9 and zlib-ng 1.0.0.

## macOS arm64

The [macOS report](gzip-native-reads-results.json) uses CPython 3.14.6 on macOS
26.6 / arm64. Streaming uses platform libz 1.2.12; complete buffers and checksums
use libdeflate 1.25.

| Workload | 3.1.3 CPU µs | 3.1.4 CPU µs | Speedup |
| --- | ---: | ---: | ---: |
| source_record_capture.json | 11.87 | 10.51 | 1.130× |
| purchase_capture.json | 5.41 | 4.94 | 1.096× |
| dataset-100-decompress | 1.65 | 1.73 | 0.955× |
| dataset-100-stream | 7.18 | 7.02 | 1.022× |
| upload-100-two-passes | 12.61 | 12.64 | 0.998× |
| dataset-20001-decompress | 89.89 | 75.97 | 1.183× |
| dataset-20001-stream | 144.55 | 138.67 | 1.042× |
| upload-20001-two-passes | 389.10 | 373.09 | 1.043× |
| dataset-40001-decompress | 538.14 | 460.58 | 1.168× |
| dataset-40001-stream | 712.06 | 682.31 | 1.044× |
| upload-40001-two-passes | 2225.46 | 2238.12 | 0.994× |
| http-bounded-expansion | 38.65 | 40.31 | 0.959× |
| journal-200-members | 55.83 | 54.78 | 1.019× |
| journal-20000-members | 6563.93 | 6266.44 | 1.047× |

The saved macOS medians show faster captured-buffer and large dataset reads.
Four rows have higher candidate CPU cost: the 100-row buffer adds 0.08 µs
(4.7%), the small upload adds 0.03 µs (0.2%), the large upload adds 12.66 µs
(0.6%), and bounded expansion adds 1.66 µs (4.3%). These results do not support a
claim of zero regressions in every macOS workload.

Shared-host variability also affects the summaries: the median of paired
baseline/candidate ratios is 1.009× for the large upload and 0.985× for bounded
expansion, versus 0.994× and 0.959× from the separate medians in the table. The
raw samples are retained; small differences should not be treated as a portable
latency guarantee.

## Linux arm64 in Docker

The [Linux report](gzip-native-reads-linux-results.json) uses CPython 3.14.8 in
`python:3.14-slim`, LinuxKit / aarch64 with glibc 2.41, pinned to guest CPU 0.
Streaming uses bundled zlib 1.3.2; complete buffers and checksums use libdeflate
1.25. This is a Docker arm64 result, with a shared host rather than a dedicated
physical CPU.

| Workload | 3.1.3 CPU µs | 3.1.4 CPU µs | Speedup |
| --- | ---: | ---: | ---: |
| source_record_capture.json | 18.28 | 10.38 | 1.761× |
| purchase_capture.json | 10.08 | 5.62 | 1.794× |
| dataset-100-decompress | 3.09 | 2.56 | 1.206× |
| dataset-100-stream | 10.55 | 10.45 | 1.009× |
| upload-100-two-passes | 20.27 | 20.97 | 0.967× |
| dataset-20001-decompress | 245.78 | 90.40 | 2.719× |
| dataset-20001-stream | 272.81 | 213.10 | 1.280× |
| upload-20001-two-passes | 804.44 | 709.59 | 1.134× |
| dataset-40001-decompress | 1041.21 | 449.77 | 2.315× |
| dataset-40001-stream | 1120.65 | 906.97 | 1.236× |
| upload-40001-two-passes | 4774.49 | 4349.89 | 1.098× |
| http-bounded-expansion | 55.80 | 38.24 | 1.459× |
| journal-200-members | 61.64 | 64.68 | 0.953× |
| journal-20000-members | 7714.62 | 7681.44 | 1.004× |

Twelve Linux CPU medians improve against 3.1.3 in this run. The small upload
adds 0.70 µs (3.4%) and the small journal adds 3.04 µs (4.9%); their median paired
ratios are 1.013× and 0.998× respectively, so these small workloads remain close
to baseline. Large one-shot reads improve 2.3–2.7×, streamed dataset reads
1.2–1.3×, and bounded uploads 1.10–1.13×. The exact wheel also uses checked
allocator hooks throughout the bundled zlib ABI. These measurements cover local
decoding and upload consumption, not network latency or every deployment
architecture.

## Historical joined-upload diagnostic

The [retained negative diagnostic](gzip-joined-upload-diagnostic.json) records
an earlier Linux run in which the 40,001-row upload took 4420.70 µs versus
3669.28 µs for 3.1.3, a 20.5% increase. It preserves the original harness SHA256,
operation and fixture generator source, environment and binary hashes, case
order, and all 31 samples for that case.

That timed consumer concatenated and retained two complete expanded outputs.
Billy's upload hashes/counts bounded chunks and streams its second pass. The
corrected benchmark measures that consumption pattern; historical joined-upload
numbers and current bounded-upload numbers have different timed contracts.
The diagnostics do not establish allocator, alignment, cache or codec causality.
The workload correction introduces no decoded-output cache: both upload passes
continue to execute native inflation.

The former [3.1.3 read qualification](gzip-read-results.md) remains a historical
record. The current runner requires actual native decoding with
`--require-native`; calling a decoder that refuses the fixture cannot satisfy
qualification. Compression tests cover malformed members, exact input
boundaries, bounded expansion, seeking, ownership, callbacks and concurrent
state. Release checks require all five native modules and authenticate the
vendored source manifests and licenses.
