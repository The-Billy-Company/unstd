---
doc_radar:
  sentinels:
    - file: native/compress/vendor/isa-l/UPSTREAM.json
      contains: ['"source_changes"', '"upstream_sha256"']
    - file: native/compress/vendor/isa-l/igzip/igzip_inflate.c
      contains: ['uint32_t long_bits;']
      absent: ['uint16_t long_bits;']
---

# Authenticated gzip dependencies

| Source group | Files and purpose |
| --- | --- |
| libdeflate 1.25 | [`libdeflate.h`](libdeflate/libdeflate.h) is the public ABI; `lib/gzip_decompress.c` validates gzip around `lib/deflate_decompress.c`; `lib/crc32.c` and `lib/utils.c` supply checksums and allocation |
| libdeflate CPU dispatch | `lib/arm/cpu_features.c` and `lib/x86/cpu_features.c` select the architecture-specific implementations in their adjacent headers |
| zlib 1.3.2 | [`inflate.c`](zlib/inflate.c), `inffast.c`, and `inftrees.c` supply streaming inflation; `adler32.c` and `crc32.c` supply its checksums |
| ISA-L 2.31.1 | [`igzip/igzip_inflate.c`](isa-l/igzip/igzip_inflate.c), static tables, and four x86_64 assembly units provide private raw inflation; the scalar C decoder remains available for older CPUs |
| zlib ABI and tables | [`zlib.h`](zlib/zlib.h), `zconf.h`, `zutil.h`, and the inflate/checksum headers describe the ABI and retained tables |

[`libdeflate/UPSTREAM.json`](libdeflate/UPSTREAM.json),
[`zlib/UPSTREAM.json`](zlib/UPSTREAM.json), and
[`isa-l/UPSTREAM.json`](isa-l/UPSTREAM.json) enumerate every retained file and its
hash, with archive URLs and digests. [`hatch_build.py`](../../../hatch_build.py)
is the compilation list: macOS uses zlib headers with platform `libz`; other
platforms compile the zlib C files with `Z_PREFIX` and Python RAW allocators.

Keep upstream names and retain exact reviewed bytes. ISA-L's three Huffman-table
loop counters use 32-bit arithmetic to match their bounds; its manifest records
that source change and the original upstream hash. Other selected upstream bytes
remain unchanged. [Supply-chain policy](../../../tests/tooling/supply_chain/policy.py)
and [release verification](../../../tests/tooling/release/verify.py) reject a
changed closure; an update requires reviewing both manifests and pinned policy.
[`libdeflate/COPYING`](libdeflate/COPYING), [`zlib/LICENSE`](zlib/LICENSE), and
[`isa-l/LICENSE`](isa-l/LICENSE) ship in distributions. Project-owned adaptation belongs in
[`../src/`](../src/README.md).

ISA-L build support is limited to Linux x86_64 and accepts NASM 2.11.01 or
Yasm 1.3.0 and newer. The build hook verifies and copies the retained sources
into its temporary directory, then applies the exact project-owned
[`isal_patch.json`](../src/isal_patch.json). Setuptools compiles the patched C
and [`isal_helpers.c`](../src/isal_helpers.c) with the same compiler as the
adapter; only four assembly objects enter a private static archive. Neither
source downloads nor a system ISA-L library are used during a package build.
