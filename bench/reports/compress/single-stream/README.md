# Single-stream gzip C qualification

The Mac ARM C adapter selects the already imported zlib-ng inflater for a narrow
class of highly repetitive streams. Ordinary dataset reads retain Apple libz;
Linux retains its existing zlib-ng SIMD reader. There is no new dependency.

These measurements compare the final implementation with the preceding
implementation on each machine. The Mac baseline was rebuilt from its exact
recorded source hashes, including the existing GIL release and per-stream lock.
The Linux baseline uses the retained previous Linux binary. The final native
sources are identical on both platforms; hashes, toolchains, versions and check
counts are recorded in [qualification.json](qualification.json).

| Workload | Mac CPU speedup | Mac wall speedup |
| --- | ---: | ---: |
| Billy bounded HTTP expansion refusal | 2.61× | 2.51× |
| 1 MiB repeated byte, 8 / 64 / 256 KiB reads | 2.15 / 2.06 / 3.12× | 2.13 / 2.04 / 3.06× |
| 1 MiB period-16 input, 8 / 64 / 256 KiB reads | 1.63 / 1.41 / 1.63× | 1.63 / 1.41 / 1.63× |
| Billy dataset upload, 20,001 / 40,001 rows | 1.00 / 1.01× | See raw samples |

The counted stream cases discard chunks and independently verify every decoded
byte outside timing. They retain bounded memory. These are warm, in-memory
measurements, without disk or network throughput. The 256 KiB repeated-byte
case reached about 14.8 GiB/s on the Mac and 11.1 GiB/s on the busy Linux server;
period-16 reached about 6.0 and 11.2 GiB/s respectively. Absolute rates vary with
hardware, data and contention; identical Mac/server runtimes are not promised.
Large JSON uploads have no substantial additional gain in this change.

The Mac is an Apple M5 Max running Python 3.14.9. Billy's actual Linux server is
an AMD Ryzen 9 7950X3D running the Python 3.14.8 CI image, with this qualification
pinned to one CPU. The provider is python-zlib-ng 1.0.0 / zlib-ng 2.2.5.
The Mac paired profile uses nine alternating rounds; Billy's 14 cases use nine
rounds and three process repetitions. The Mac Billy run passes the existing
30% wall guard.

The server was running other jobs. Two process-level comparisons exceeded that
wall guard on varying cases; their raw results are retained in `busy-*.json.gz`.
To reduce time-varying contention, the final server qualification uses persistent
workers and alternates the candidate and baseline for each case, with 11 rounds
and jointly calibrated batches of at least 30 ms CPU. All 14 cases pass the
same 30% wall guard. CPU speedups range from 0.948× to 1.056×, with a 1.006×
geometric mean. The separate 21-case read profile ranges from 0.955× to 1.072×.
This supports throughput near parity on the server; it does not establish a
Linux speedup. See [Linux timings](billy-linux-interleaved.json),
[read timings](reads-linux16.json) and [backend proof](metadata-linux16.json).

The C selection probe reads at most 8 KiB of already held compressed input and
4096 tokens, requiring a complete final dynamic block, valid trees, a real EOB,
and predominantly length-258 matches at a constant distance of at most 16 bytes.
It selects only before body decoding, with at least 16 KiB of actual output
space. Smaller prefix calls and incomplete or unsupported probes retain Apple
libz. It does not read the source, generate or cache expanded bytes, inspect an
ISIZE allocation hint, change a read budget, or replay a failed decoder.
Rare late-rejected bodies cost about 3–4 µs once (up to 16% in the edge
measurements); no universal speedup for arbitrary inputs is claimed.

The qualified behavior includes source read calls and position, output bounds,
CRC/size failures, source exception identity, concatenations, rewind/seek,
legacy tails, mutable-input snapshots and staged public `readinto` failures.
The module and each live decoder independently retain the native API handle;
the immutable heap type participates in cycle collection. Native initialization
OOM propagates before decoding and preserves input for retry.

Validation:

- Mac Python 3.14: 761 compression checks passed.
- The active editable build also passes 143 native reader, thread and selection checks.
- Linux Python 3.14: 698 passed; 62 Darwin-specific skips and one optional mypy skip.
- Separately compiled Mac Python 3.12 and 3.13: 370 selected checks each, no skips,
  plus debug allocation, OOM rollback and GC lifetime checks.
- Wheel and sdist-rebuilt wheel on Python 3.12 and 3.14: 155 packaging checks each.
- 168 additional public byte/error/callback histories match the exact baseline.
- 302,848 independent word-group oracle cases, 12,085 body comparisons and 7,397
  truncated prefixes pass. ASan/UBSan pass 152,030 guard-page classifier calls
  and 130,944 group calculations.
- 103 relevant benchmark gates pass; changed Python/stub files pass lint and
  strict typing checks.

[Mac read samples](reads-macos16.json), [Mac Billy samples](billy-macos16.json),
[cross-version receipt](native-python-versions.json),
[packaging receipt](packaging-receipt.json),
[public histories](public-traces-final16.json),
[OOM receipt](ownership-oom-final16.json), [GC receipt](gc-cycle-final16.json)
and [edge timings](edges-final16.json) retain the detailed evidence.
`shape-oracle.json.gz` and the compressed logs retain classifier results.
`reproduction-sources.tar.gz` archives the qualification scripts and frozen
header; scripts retain the original qualification paths recorded in the receipts.
The regular paired profiles can be repeated with:

```console
python bench/profiles/gzip_reads.py --source /tmp/current --baseline-source /tmp/before --runs 9 --batch-ms 15 --require-native --output /tmp/reads.json
python bench/profiles/billy_gzip.py run --source /tmp/current --baseline-source /tmp/before --fixtures bench/workloads/billy_gzip.json.gz --rounds 9 --repetitions 3 --max-slowdown 1.30 --output /tmp/billy.json
```

Alternative whole-stream backends and wrapper/output-buffer changes evaluated
during development did not improve ordinary Mac uploads enough to ship.
The implementation retains the interpreter's reader, buffering and framing.
The earlier [port contract matrix](../port-contracts.md) continues to describe
external codec coverage.
