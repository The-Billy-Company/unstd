---
doc_radar:
  sentinels:
    - file: src/unstd/compress/gzipx.py
      contains: ['_stdlib.GzipFile.__init__(', 'zlib.decompressobj(wbits=31)', 'del decoder']
    - file: bench/gzip_reads.py
      contains: ['requested source was not imported', 'decoded bytes differ from the source']
---

# Gzip read workload measurements

Recorded on 2026-10-22, macOS arm64, with zlib-ng 1.0.0 and stdlib zlib 1.2.12.
The comparison source is commit `15036df`, saved before this change. Full samples,
source hashes, fixture hashes and observed C calls are in
[the Python 3.13 results](gzip-read-results.json) and
[the Python 3.14 results](gzip-read-py314-results.json).

## Why the earlier improvement did not fit Billy

The earlier benchmark improved large level-6 writes, while one-shot reads ran at
0.56× stdlib speed. Billy already compresses outbound requests directly through
zlib with `wbits=31`, explicitly using level 1 for its Claude/Gemini request bodies.
Its gzip reads include datasets, HTTP bodies limited to a maximum output size,
concatenated journal members, and uploads that rewind a stream between hashing
and transmission. A faster large write does not establish a benefit for these
operations. Billy's inspected Python 3.14 environment does not install the
compression extra, and its existing gzip paths remain in place.

The native codec itself was slower on the saved 10–18 KiB JSON captures. On
larger, highly compressible inputs, the native one-shot reader's allocation path
added further cost. Streaming added duplicate reader construction and the
compatibility adapter around native inflation.

## Change

File reads now initialize the running interpreter's stdlib gzip reader directly.
This keeps lazy bounded output, CRC/trailer checks, concatenated members, seeking
and file ownership intact. The unused native inflater adapter is removed.

Complete single-member `bytes` and `bytearray` inputs use zlib's C gzip parser,
including optional trailing zero padding. Other buffer types and subclasses use
stdlib directly to preserve its element-based slicing and callback behavior.
Concatenated members and C decoder failures use `gzip.decompress`, preserving
stdlib's header permissiveness and exact error diagnostics. Temporary decode
buffers are released before fallback. Native compression is unchanged; each
candidate worker records an actual `zlib_ng.zlib_ng.compress` call.

## Python 3.13.14

Each value below is the median of seven measurements; each measurement retains
two calibrated batch samples and reports the minimum batch mean. Variant order
alternates on each repetition. Setup, profiling, process startup and IPC are
outside the measured operation. All variants receive identical compressed bytes
and must recover the independent original bytes before timing.

| Read operation | Previous µs | Current µs | Stdlib µs | Previous/current |
| --- | ---: | ---: | ---: | ---: |
| Saved source JSON, 18,255 bytes | 20.8 | 11.8 | 13.8 | 1.76× |
| Saved purchase JSON, 9,575 bytes | 10.8 | 6.9 | 8.6 | 1.56× |
| 100-row dataset, one shot | 3.6 | 2.3 | 3.3 | 1.56× |
| 100-row dataset, stream | 17.6 | 11.0 | 10.0 | 1.60× |
| 100-row upload, two passes | 31.3 | 20.4 | 19.6 | 1.54× |
| 20,001-row dataset, one shot | 170.3 | 119.7 | 126.1 | 1.42× |
| 20,001-row dataset, stream | 185.3 | 140.4 | 139.7 | 1.32× |
| 20,001-row upload, two passes | 303.4 | 251.0 | 266.7 | 1.21× |
| 40,001-row dataset, one shot | 921.2 | 587.4 | 600.7 | 1.57× |
| 40,001-row dataset, stream | 948.1 | 771.1 | 774.2 | 1.23× |
| 40,001-row upload, two passes | 1760.1 | 1444.0 | 1436.4 | 1.22× |
| HTTP output-limited expansion | 13.9 | 8.9 | 8.6 | 1.57× |
| 200 journal rows, 2 members | 69.1 | 55.9 | 54.1 | 1.24× |
| 20,000 journal rows, 200 members | 6809.1 | 6055.4 | 6049.7 | 1.12× |

Every measured case improved against the previous implementation. Larger streams
are close to direct stdlib; small streams retain about 1 µs of wrapper overhead.
The change removes the previous regression, but does not establish a reason for
Billy to replace its existing stdlib streams. These are local CPU workloads using
saved bytes and generated consumption shapes, not production request latency.

## Python 3.14.6 and limits

The second interpreter run had substantial scheduling interference: 82 of the
candidate's 98 measurements had a greater-than-25% batch spread, as did 76 of 98
measurements for each comparator. The retained CPU samples distinguish that
interference from actual codec work; CPU time does not replace wall latency.
The saved JSON cases still improved, but these wall medians are weaker evidence
than the Python 3.13 run.

| Read operation | Previous µs | Current µs | Stdlib µs |
| --- | ---: | ---: | ---: |
| Saved source JSON | 19.8 | 12.9 | 14.2 |
| Saved purchase JSON | 13.0 | 9.6 | 10.5 |
| 40,001-row dataset, one shot | 955.6 | 846.2 | 604.8 |
| HTTP output-limited expansion | 26.7 | 40.8 | 40.6 |

The bounded expansion case is a real limitation: Python 3.14's default buffered
reader can inflate 128 KiB while satisfying the 2,009-byte read. Native inflation
handles this highly repetitive payload faster than stdlib. Both current and
direct stdlib follow the same path; this change does not improve that existing
Billy operation. This is another reason to retain Billy's present gzip choices
instead of enabling the extra based on a large-write benchmark.

A temporary 8 KiB buffer improved this bounded shape, but changed malformed
DEFLATE delivery history: a valid 9 KiB stored block followed by an invalid block
raised before returning any bytes with Python 3.14's default buffer; the smaller
buffer returned 8,190 bytes in seven-byte reads before raising. The implementation
retains the interpreter's buffer, and a regression test pins this observable
behavior against the running stdlib.

## Validation and reproduction

The compression suite passes on CPython 3.12–3.14 with zlib-ng installed, and in a
base environment without the extra. Coverage includes exact malformed-input
diagnostics, reserved flags and ignored header checksums, non-byte buffer
slicing, subclass callbacks, concatenated-member damage, short sources, seeking,
file ownership and read bounds. A 64-byte read from an 11 MiB expanded member
stays below a 1 MiB traced allocation budget. Benchmark tests reject incorrect
decoded bytes and incorrect comparison-source imports. Ruff and mypy pass for
the changed source and harness.

Save the complete source tree so importing the shared benchmark timer can load
the rest of the package as usual:

```console
mkdir -p .local/gzip-read-before
git archive 15036df src/unstd | tar -x -C .local/gzip-read-before
.venv/bin/python bench/gzip_reads.py --baseline-source .local/gzip-read-before/src --runs 7 --output /tmp/gzip-reads.json
```

Repeat `--fixture PATH` to add exact saved payloads. The recorded runs add Billy's
`source_record_capture.json` and `purchase_capture.json` from its test fixtures.
Their bytes are not copied into this repository; the reports retain hashes and
sizes. Generated cases reproduce dataset counts, bounded HTTP reads, concatenated
line iteration, 256 KiB upload reads and a second pass after `seek(0)`. Run with
the desired Python environment and the compression extra installed; the report
records interpreter, platform, dependency versions, source hashes and routes.
