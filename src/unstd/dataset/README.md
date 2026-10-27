# Indexed JSON datasets

Build a JSON corpus once, then read selected records without loading or parsing
all of it again. `unstd.dataset` provides a read-only Python sequence backed by
one portable file. No optional dependency is required.

```python
from unstd.dataset import Dataset, build
from unstd.serde import ndjson

with open("events.jsonl", "rb") as source:
    count = build("events.dataset", ndjson.iter_loads(source))

with Dataset("events.dataset") as events:
    total = len(events)
    first_page = events[:50]
    selected = events.take([1000, 12, -1, 12])
    for batch in events.batches(256, start=100):
        process(batch)
```

`take` preserves order and duplicates. Integer indexing, negative indices, and
slices follow Python sequence rules. Every decoded value belongs to the caller;
mutating it cannot change the file or another read.

## Choose the access pattern

| Need | API | Memory retained by the operation |
| --- | --- | --- |
| Count records | `len(events)` | No decoded records |
| Read one record | `events[index]` | One decoded record |
| Materialize a page | `events[start:stop:step]` | Selected decoded records |
| Select arbitrary rows | `events.take(indices)` | Requested indices, encoded rows, and decoded records |
| Scan one row at a time | `iter(events)` | One decoded record |
| Scan a lazy slice | `events.iter_rows(start, stop, step)` | One decoded record |
| Process contiguous batches | `events.batches(size, start=0, stop=None)` | One batch of encoded and decoded rows |
| Check a transferred artifact | `events.verify()` | Fixed checksum buffers; no JSON decoding |

Use snapshots for repeated sampling, pagination, evaluation, and scans that need
a total count before starting. For one prefix or one sequential pass over JSONL,
`ndjson.iter_loads` avoids building another artifact. `ndjson.dump(events, stream)`
exports rows to JSONL; gzip streams work through the same existing APIs.

The [performance report](../../../bench/reports/dataset/README.md) compares eager
JSONL loading, streaming, indexed access, and native versus portable execution.
Build cost, file size, memory, and slower cases are reported separately.

## Build and publication

`build(path, rows, *, max_row_bytes=16_777_216)` consumes an iterable once and
returns its row count. The iterable can be a generator, a JSONL reader, or a
reader over compressed data. The artifact contains its records and index;
changing or deleting the original source cannot invalidate it.

JSON encoding rejects nonfinite floats. Tuples and object keys follow stdlib
`json.dumps` normalization; lone surrogates are escaped. The positive integer
`max_row_bytes` bounds each accepted encoded record, excluding its framing
newline. Encoding allocates a complete row before checking that limit; this is
not a hard allocation cap on the producer or encoder.

Rows stream directly through `fs.atomic_writer`. Offsets spool to a temporary
file, keeping Python memory independent of corpus length. Construction needs
space for the new artifact and its temporary offset table, alongside any old
artifact. New files request private permissions; existing permissions survive
replacement.

A failure before replacement preserves the previous artifact and cleans up
scratch files. Cancellation reported after replacement can leave the new file
committed, with the publication note from `fs.atomic_writer`. Concurrent builds
use last-replacement-wins semantics. File contents are synchronized before
replacement; persistence of the directory entry across power loss is not
promised.

## Reading and integrity

Opening checks the version, file geometry, and indexed endpoints without
scanning records. Length and offset lookup are constant time. The file is mapped
read-only; the OS loads pages on demand. Mapped address space grows with file
size, while decoded Python memory grows with the requested rows or batch.

Use `with Dataset(path)` or call `close()`. Repeated closure is safe; closed
reads and pending iterators raise `ValueError`. Iterators have independent
positions. Read-only calls can share a handle across threads, with the caller
coordinating closure with active reads. No mapping views escape to callers.

Artifacts must only change through atomic replacement. Editing or truncating a
mapped file in place is unsupported. Where the OS permits replacing open files,
existing readers keep their previous snapshot. An OS sharing refusal leaves the
old file intact and is reported to the builder.

Every selected offset and row terminator is checked before reading. `verify()`
also checks the complete index and SHA-256 body checksum without JSON decoding.
Use it to detect accidental corruption after transferring an artifact. This is
an integrity check, not authentication against someone who can replace both the
file and its hash. Invalid format, offsets, or checksum raise `ValueError`;
malformed selected JSON retains the JSON decoder's exception.

## Implementation and acceleration

The public imports are `Dataset` and `build` from `unstd.dataset`. The
[`reader.py`](reader.py) module owns sequence semantics, selection, batching, and
resource ownership. [`storage.py`](storage.py) owns the versioned file format,
bounded builder, mapped byte access, and integrity checks. Supporting names are
private; callers need only the package imports.

The optional bundled [native kernel](../../../native/dataset/README.md) fuses
checked offset lookup and copying for single and batched reads. Contiguous
batches also reuse `serde.ndjson` and its available decoder acceleration. JSON
parsing stays in the existing serialization layer. Compiler-free installs keep
the complete Python implementation and identical public behavior.

On POSIX, the native kernel also owns a private, read-only OS mapping of the
already-published artifact. Opening that mapping does not synchronize the file
again. This avoids CPython's macOS mapping-constructor synchronization
workaround, tracked in [CPython issue 113861](https://github.com/python/cpython/issues/113861).
Windows and compiler-free installs use stdlib `mmap`, retaining its platform
behavior. Keep a dataset open when making repeated reads so mapping setup is
paid once. The [access qualification](../../../bench/reports/dataset/access.md)
separates mapping setup from native offset operations and reports both paths.

```sh
python -m pytest tests/suites/product/dataset -q
python -m bench.profiles.dataset --output /tmp/dataset-proof.json
python -m bench.speeds check --group dataset
```
