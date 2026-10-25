# Gzip API and native edge contracts

`test_gzipx.py` compares gzip values, meaningful headers, error families, file modes, and caller-owned streams with the running stdlib. It includes real compressed NDJSON pipelines and consumer typing checks. Compressed byte identity across engines is not required.

`test_rfc1952_layout.py` hand-packs header, FNAME and `CRC32 || ISIZE` trailer fields at every level. It checks fixed-mtime determinism, empty and mixed-level members, and damage in a later member against stdlib's delivered bytes and exception class.

`test_gzipx_edges.py` checks bounded reads, short sources, native read bypass, callback errors, and cleanup. Native routing checks require zlib-ng; typed pipelines require msgspec and typing checks require mypy.

`test_native_build.py` checks that default platform/vendored inflater selection
stays intact, alternate static archives require an exact reviewed digest and
private symbols, and the qualification-only option cannot enter release wheels.
`test_native_buffers.py` compares native IEEE CRC32 and incremental updates with
stdlib across short, block-boundary and large inputs.

`test_module_ports.py` proves package and qualified imports keep the original
codec/archive modules, classes and exceptions. It covers lazy base imports,
missing optional backends, versioned stdlib Zstandard, native dictionaries and
frame boundaries, bounded BZ2/LZMA state, borrowed text streams, stock zlib bytes
and compressor copies, and ZIP metadata/stream ownership.

Qualified package-child tests also preserve canonical module metadata and native
exception identity, exercise a nested ZIP package, keep optional CFFI loading
lazy, and refuse filesystem replay for a missing canonical child.

`test_zlibx.py` compares window modes, checksums, compressor copies, dictionaries,
argument callbacks and interpreter-specific incremental decoder state.
`test_flush_modes.py` checks native C-integer overflow, stream continuation,
coercion timing and closed/read-handle behavior for gzip flushes.

`test_libarchive_port.py` checks original package/submodule, FFI and exception
identity, lazy native loading, missing binding/DSO errors, real compressed
archive blocks and headers, and shared warning/diagnostic callbacks. These
checks preserve upstream behavior; application archive policy remains separate.

`test_native_threads.py` checks shared bounded inflaters, concurrent
reinitialization, Python-thread progress during large inflation, and reentrant
codec-error translation. Native crash and deadlock probes run in bounded
subprocesses.

`test_native_adaptive.py` checks the Darwin ARM64 structural codec choice with
independently packed RFC 1951 traces and real period 1–16 streams. It covers
ordinary and nonfinal streams, distance limits, truncated and malformed dynamic
headers, actual long-match usage and complete-body/token/input caps, the first
output budget, one-time selection, EOF and legacy input tails, mutable/aliased
buffers, late provider binding, GC type traversal, and reentrant reset callbacks.
The optional provider uses the shared required-backend policy; these native
selection checks skip explicitly on other operating systems and architectures.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/compress/unit -q
```

See [the module test overview](../README.md) for related evidence.
