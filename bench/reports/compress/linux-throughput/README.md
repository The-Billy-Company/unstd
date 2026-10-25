# C gzip throughput on Linux and Mac

Billy’s large request encodes are **1.9–2.0× faster on both machines**. Full dataset JSON → gzip → base64 materialization is **1.4–1.5× faster**. On the actual Linux server, large two-pass upload workflows improve **20–21%**, and a successful bounded HTTP read improves **26%**. These ratios compare the saved, already accelerated implementation with the current C code on each machine.

Each persistent worker imports an isolated installation. Timings alternate per case with common calibration, 11 rounds and at least 30 ms of thread CPU per batch. Output, source receipts, native calls and selected engines are checked outside timing. The 14 original Billy stream fixtures are unchanged; the separate request fixture contains eight request/checkpoint cases and three dataset cases. `gzipx` runs the six whole-buffer cases.

| Operation / fixture | Linux wall speedup | Mac wall speedup | Current / prior gzip size |
|---|---:|---:|---:|
| `request-direct-4k-level6` | 1.249× | 1.156× | 1.0046× |
| `request-direct-128k-level6` | 1.898× | 2.005× | 1.0061× |
| `request-direct-512k-level6` | 1.952× | 1.895× | 1.0093× |
| `dataset-materialize-100` | 1.134× | 1.070× | 0.8952× |
| `dataset-materialize-20001` | 1.542× | 1.489× | 0.7801× |
| `dataset-materialize-40001` | 1.448× | 1.426× | 0.7939× |

The request fixture labels describe the requested payload size; actual JSON body sizes are 6,506, 132,690 and 524,746 bytes. Large dataset JSON bodies are 1,286,747 and 2,606,747 bytes. Request gzip output increases by 0.46–0.93% in these fixtures; large dataset output is 20.6–22.0% smaller. This is measured quality on the fixtures, not a universal compressed-size bound.

| Original Billy streaming operation | Linux wall speedup |
|---|---:|
| `dataset-query-100` | 1.002× |
| `dataset-upload-100` | 0.979× |
| `dataset-query-20001` | 0.992× |
| `dataset-upload-20001` | 1.205× |
| `dataset-query-40001` | 0.999× |
| `dataset-upload-40001` | 1.207× |
| `http-bounded-success` | 1.258× |
| `http-expansion-refusal` | 0.987× |
| `journal-replay-200-1-members` | 0.981× |
| `journal-replay-200-2-members` | 0.971× |
| `journal-write-flush-200` | 1.010× |
| `journal-replay-20000-1-members` | 1.002× |
| `journal-replay-20000-2-members` | 1.037× |
| `journal-write-flush-20000` | 0.999× |

The broader 21-case [Linux read matrix](linux-reads.json) records 1.22–1.27× gains on large streams and two-pass reads. Its slowest synthetic control, the period-1 stream with 256 KiB reads, has about 5.8% higher latency (roughly 4.3 µs); the original Billy controls stay within about 3% of prior throughput. The Mac stream controls retain their previous implementation and gains. See [Linux Billy samples](linux-billy.json), [Mac Billy samples](mac-billy.json), [Linux encode samples](linux-encodes.json) and [Mac encode samples](mac-encodes.json).

The Linux run uses the actual Ryzen 9 7950X3D server, 128 GiB RAM, one pinned CPU (28), Python 3.14.8, GCC 12.2, zlib-ng 2.2.5 and local Yasm 1.3.0. The Mac is an Apple M5 Max with Python 3.14.9 and Apple Clang 21. Exact runtime, compiler flags and binary hashes are in [Linux platform metadata](linux-platform.json) and [Mac platform metadata](mac-platform.json). The server was shared with other jobs; both CPU and wall samples are retained.

The C changes are deliberately narrow:

- Whole-buffer `gzipx.compress` admits exact `bytes`, exact integer levels 6 or 9 and exact integer `mtime=0`. Conservative entropy, period and compact-ASCII-JSON guards retain the existing provider elsewhere. Level 6 uses an owned three-entry HT finder with libdeflate level 1; level 9 uses its level 6 HC implementation. The libraries use different effort scales. Every call owns its compressor and uses checked Python RAW allocation. Stateful writers, flushes and compressor copies retain their existing provider.
- Linux x86_64 release builds include a private ISA-L 2.31.1 decoder. Supported CPUs require SSE4.2 and PCLMUL; admission happens before decoding, with at least 8 KiB input, 16 KiB output space, a dynamic first block and at least nine distance codes. Other stream shapes keep zlib-ng. macOS retains its previously qualified Apple/zlib-ng choices.
- The owned core edits cap internal decoding to the requested output, validate complete code trees and preserve diagnostics. Cold symbol checks use retained history and limit packed lookups to the first proved literal. Header-owned bits are never returned by pointer rewind. Input exhaustion flags retain the reference provider’s behavior, including its pending-literal quirk.
- Each decoder retains its own lock and state. Gzip framing, optional headers, CRC/ISIZE, concatenation, seeking, short reads, source callbacks and ownership remain governed by the running interpreter. Public `readinto` keeps its staged failure behavior. Stream failures are latched; decoding is not replayed and expanded data is not cached across rewinds.

The original `bz2`, `lzma`, `zlib`, `tarfile`, `zipfile`, `zstd`, `zstandard` and `libarchive` ports retain the original provider objects. Use the original `zlib` port when compressed-byte identity is needed for Billy’s 64 KiB checkpoints. The request driver keeps strict checkpoint byte checks: zlib-ng’s pre-existing 512 KiB chunk-boundary variation remains a reported contract failure in the `zlibx` lane rather than being accepted by a weaker oracle.

Validation includes **31,840 raw traces and 14,592 public histories**, independently prefixed with zlib and zlib-ng output. Both [NG-prefix](linux-budget-ng.json) and [stdlib-prefix](linux-budget-stdlib.json) reports have zero mismatches, including native error wording, exact output pieces, EOF/input flags, unused tails, source calls/position, exception ownership and failed destination buffers. Each corpus includes 3,492 traces selecting ISA-L. Stored, fixed and dynamic blocks, malformed trees, short headers, truncation, tiny tail budgets and later members are covered.

The complete Mac compression suite passes 924 checks; 33 Linux-only checks skip. Its build/benchmark gates add 77 passing checks. The complete Linux suite with the optional codec dependencies installed passes 895 checks, with 62 Mac-only skips. Focused new coverage includes 72 encoder checks, 91 public read histories and 33 Linux raw decoder histories. Ruff and mypy pass for the changed compression/profile surfaces. [Build and test logs](build-and-tests.tar.gz) also retain the Mac encoder ASan/UBSan checks and 240 boundary cases.

Packaged artifacts are qualified independently of editable installs: **213 packaging checks pass on each of Python 3.12 and 3.14**, including direct wheel installs, offline sdist rebuilds, all optional extras, types, licenses and RECORD. An actual clean-source Linux wheel uses the same reviewed hook and source archive, has ISA-L active and passes its installed compression suite. ISA-L symbols are private, there is no dynamic ISA-L dependency, and the wheel has a non-executable stack and no text relocations. See the [packaging receipt](packaging.json) and [verified artifact manifest](verified-dist-manifest.json). Source builds need local NASM ≥2.11.01 or Yasm ≥1.3.0 to enable the Linux streaming route; release builds fail if that tool is missing. No Python ISA-L package is required at runtime.

Original vendored files remain unchanged. The build authenticates their inventories, stages private copies, and applies the owned patch only there. The libdeflate v1.25 archive is pinned to `fed5cd22f00f30cc4c2e5329f94e2b8a901df9fa45ee255cb70e2b0b42344477`; ISA-L v2.31.1 to `e1d5573a4019738243b568ab1e1422e6ab7557c5cae33cc8686944d327ad6bb4`. Their [libdeflate source archive](https://github.com/ebiggers/libdeflate/releases/tag/v1.25) and [ISA-L source archive](https://github.com/intel/isa-l/tree/v2.31.1) are upstream references. The owned ISA-L patch is `9b537e1df67e96f78bd683db3d2d2c6de9709398687b7e4c3f6a043686baf905`; its staged C output is `bd2afa52d7da3f909e2aa103bdf68e26521453ae103da52fccf42e9db9b6dbc0`. [Exact build inputs](linux-build-source-manifest.json) and [reproduction drivers](reproduction-sources.tar.gz) are retained.
