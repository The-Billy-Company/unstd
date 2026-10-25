# Authenticated gzip dependencies

| Source group | Files and purpose |
| --- | --- |
| libdeflate 1.25 | [`libdeflate.h`](libdeflate/libdeflate.h) is the public ABI; `lib/gzip_decompress.c` validates gzip around `lib/deflate_decompress.c`; `lib/crc32.c` and `lib/utils.c` supply checksums and allocation |
| libdeflate CPU dispatch | `lib/arm/cpu_features.c` and `lib/x86/cpu_features.c` select the architecture-specific implementations in their adjacent headers |
| zlib 1.3.2 | [`inflate.c`](zlib/inflate.c), `inffast.c`, and `inftrees.c` supply streaming inflation; `adler32.c` and `crc32.c` supply its checksums |
| zlib ABI and tables | [`zlib.h`](zlib/zlib.h), `zconf.h`, `zutil.h`, and the inflate/checksum headers describe the ABI and retained tables |

[`libdeflate/UPSTREAM.json`](libdeflate/UPSTREAM.json) and
[`zlib/UPSTREAM.json`](zlib/UPSTREAM.json) enumerate every retained file and its
hash, with archive URLs and digests. [`hatch_build.py`](../../../hatch_build.py)
is the compilation list: macOS uses zlib headers with platform `libz`; other
platforms compile the zlib C files with `Z_PREFIX` and Python RAW allocators.

Keep upstream names and bytes unchanged. [Supply-chain policy](../../../tests/tooling/supply_chain/policy.py)
and [release verification](../../../tests/tooling/release/verify.py) reject a
changed closure; an upgrade requires reviewing both manifests and pinned policy.
[`libdeflate/COPYING`](libdeflate/COPYING) and [`zlib/LICENSE`](zlib/LICENSE)
ship in distributions. Project-owned adaptation belongs in
[`../src/`](../src/README.md).
