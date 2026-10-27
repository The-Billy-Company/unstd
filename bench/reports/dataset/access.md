# Dataset storage and native qualification

This comparison measures the actual public dataset API against a standalone
SQLite reference and the same mapped implementation with its native backend
disabled. [Raw results](access.json) retain all seven matched rounds, lane orders,
source and binary hashes, backend versions, and observed native entrypoints.

## Reproduce

```sh
uv sync --extra serde --group dev --reinstall-package unstd
uv run --no-sync python -m bench.profiles.dataset_access --output /tmp/dataset-access.json
```

A C compiler or a package with the native dataset extension is required for this
three-way comparison. The ordinary package remains usable without that extension.
The default fixture has 25,000 deterministic JSON objects, each with a 256-byte
text field, numeric fields, and a nested list. No external checkout is required.

## Recorded result

The recorded host uses Python 3.14.9 on
macOS-26.6-arm64-arm-64bit-Mach-O, SQLite 3.53.1, and native
kernel version 2. Times below are medians in milliseconds;
speedups divide the displayed implementation medians before rounding.

| Operation | SQLite reference | Mapped Python | Mapped native | Native vs SQLite | Native vs Python |
| --- | ---: | ---: | ---: | ---: | ---: |
| Open, count, close | 0.263 | 3.987 | 0.120 | 2.19× | 33.27× |
| 1,024 scalar index reads | 2.817 | 0.959 | 0.757 | 3.72× | 1.27× |
| Take 64 scattered rows | 0.164 | 0.065 | 0.044 | 3.69× | 1.46× |
| Take 4,096 scattered rows | 12.405 | 3.571 | 2.345 | 5.29× | 1.52× |
| Full lazy scan | 21.083 | 34.457 | 20.014 | 1.05× | 1.72× |
| Full scan in 1,024-row batches | 29.055 | 25.390 | 15.402 | 1.89× | 1.65× |

Building the mapped artifact took **60.340 ms**, compared with
**82.754 ms** for SQLite: **1.37× faster**.
Both include streaming source decoding, finite JSON encoding, disk-backed index
construction, file synchronization, and atomic publication. SQLite additionally
commits its transaction and copies a finalized scratch database through the
atomic writer; the new format streams directly to that writer and spools only
the offset table.

The JSONL source was **7,824,444 bytes**. Its mapped snapshot was
**8,024,508 bytes** (**2.56% overhead**),
versus **8,568,832 bytes** for SQLite
(**9.51% overhead**). The eight-byte offset per
record has a larger percentage cost for tiny scalar rows; these figures describe
this fixture rather than a universal storage ratio.

The measured package was loaded from an isolated installation of the built
wheel, whose SHA256 is
`94e710a726b7447f68af10f5d122d95f0f33df5cbb843b77abf45f93611873c9`.
The benchmark asserted that import boundary before capture. Reported module
origins use public package-relative labels; content hashes identify the exact
loaded code and native binaries independently of the checkout location.

## What native code changes

JSON decoding stays in the existing serializer. The C extension checks buffer
geometry and selected offset ranges, rejects invalid row framing, and copies
owned bytes for Python to decode. The public request semantics, JSON values,
and portable implementation stay the same.

On POSIX, a private native owner also maps immutable files read-only. The
[CPython 3.14 mmap constructor](https://github.com/python/cpython/blob/3.14/Modules/mmapmodule.c)
requests a full filesystem synchronization for file-backed mappings on macOS.
The earlier [diagnostic capture](access-pre-map-owner.json) measured that cost:
fresh mapped open/count/close took 4.05 ms. Native ownership reduced it to
**0.120 ms** in the final capture. The portable
control still uses the stdlib mapper, taking
**3.987 ms** here. The private owner is read-only,
retains its mapping after the input descriptor closes, and refuses to close
while an exported buffer exists. Windows uses the stdlib mapping owner while
retaining the checked native offset operations.

Untimed observation verified actual C calls: fresh open reached `map` once,
1,024 scalar reads reached `row` 1,024 times, each `take` reached one C batch
call, full lazy iteration reached `row` 25,000 times, and batch iteration reached
`span` 25 times. Portable and SQLite controls recorded zero dataset-kernel calls.
All three controls still use the same installed JSON decoder family. The SQLite
batch control uses the same bulk NDJSON decoder to avoid an artificially weak
per-row decoding baseline.

## Controls and limits

Each timing round visits all three implementations in a rotated order, reversed
on alternating rounds. Exact selected records and independent arithmetic scan
aggregates are checked before, during, and after the campaign, outside timing.
The actual mapping implementation is selected before construction, so the Python
control cannot accidentally reuse a native-owned mapping. The package sources,
JSON helper modules, C source, and loaded native binaries must match before and
after measurement; a change refuses publication. No tracer or binding counter
runs inside the timed region.

These are warm-cache local measurements on one synthetic shape. Indexed and scan
operations reuse open handles; opening and closing are measured separately.
Fixture creation and output checking are excluded. The portable mapper's slow
macOS opening cost remains a measured loss relative to SQLite, and the portable
full scan was slower than SQLite too. Native full-scan performance was close to
SQLite in this capture; the larger wins belong to selected access and batches. The public
[streaming comparison](README.md) covers a larger nested-record shape and keeps
any cases where a one-off streaming read is faster. Native acceleration is
optional, and the table makes no promise about another host or workload.
