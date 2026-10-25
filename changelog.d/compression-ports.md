Optimize gzip streaming for dataset, upload, HTTP and journal use cases:
Linux selects zlib-ng's SIMD inflater; macOS retains platform libz and releases
the GIL safely during large native reads. Per-decoder locking protects state,
and oversized flush modes cannot silently finish a stream. Interpreter framing,
bounded reads, member checks, error delivery and caller-owned buffers remain
covered by compatibility tests.

Add `zlibx` for accelerated zlib wire operations, with the interpreter's exact
incremental decoder. Add original-module ports for bz2, lzma, stock zlib,
zipfile, tarfile, optional python-zstandard and libarchive-c, and Python 3.14's
compression.zstd. Original modules retain class, exception, context and qualified
import identity. Stock zlib remains available for compressed-byte hash contracts.

Retain paired Mac/Linux throughput evidence, an external-codec guarantee
matrix, frozen consumer fixtures and a four-platform/interpreter regression
guard. Four independent upload streams measured 1.83× faster on Mac and 4.58×
on the Linux server; gains depend on workload and hardware.
