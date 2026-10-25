# Linux atomic-commit proof

This is an explicit experiment, outside `hatch_build.py` and the public fs
dispatch. It measures whether moving a complete one-shot atomic write into C
reduces the Python work in Billy's small state, token, report, and checkpoint
writes. It does not claim an improvement until the Linux consumer measurements
establish one.

```python
import _fs_experiment

_fs_experiment.atomic_write(path, encoded_bytes, durable=False)
```

The result is `None`. A comparison with public `fs.atomic_write` must include a
Python wrapper returning the destination `Path`. Text cases must include the
encoding operation inside the timed wrapper. Parent checks and creation are
already inside the C call and must not be excluded from the comparison.

## Retained mechanics

The C call checks or recursively creates parents, preserves the existing
destination's mode from scratch creation, uses exclusive scratch creation next
to the destination, writes every byte through short writes and interruptions,
calls real file `fsync`, closes the file, and renames it. New files request
`0600`, filtered by the process umask. Existing modes are copied with descriptor
`fchmod`; the proof never changes the process umask.

Scratch names use the same 8-hex-character entropy suffix as the Python path,
including the same filename-length budget. `O_EXCL` retries collisions. Owned
descriptors close and scratch files are discarded on pre-commit errors. Linux
`close` errors are reported without retrying a descriptor that may have been
reused. Blocking filesystem work releases the GIL.

`durable=True` retains best-effort directory syncing after the rename.
`durable="strict"` acknowledges newly created parent entries, preflights syncing
the final directory, and syncs it again after commit. A failed post-commit sync
reports that the replacement has already committed. No `fdatasync`, omitted
sync, batched commits, or payload cache is used.

## Admission and limitations

- Linux CPython with its ordinary GIL; this is not a portable module.
- A filesystem path and immutable `bytes` payload. The lexical filename must
  already be normalized, nonempty, without a trailing slash or NUL. `Path`
  conversion belongs in the timed wrapper. Arbitrary text encoding, newline
  handling, stream contexts, and traversal are not implemented here.
- Trusted parent components and destination namespace, as required by the
  public atomic-write path. The one-shot writer does not add directory-fd
  namespace confinement.
- Durability is exactly `False`, `True`, or `"strict"`. The proof deliberately
  rejects other values rather than imitating every invalid-input behavior.
- Python signals are checked after the C transaction completes. Cancellation
  can therefore arrive after commit; the exception receives a committed-state
  note. This differs from Python-level cancellation checkpoints and is an
  explicit reason this proof is not a drop-in backend.
- The error-stage note and filename reporting are experimental. Complete error
  and cleanup-note parity, signal timing, alternate interpreters, allocator
  failures, and platform-specific pathname corner cases require a separate
  production qualification before integrating this module.

## Build and qualify

Run these commands inside Linux with a C compiler and Python headers:

```console
python native/fs/experiments/build.py --output-dir /tmp/fs-proof --observer
python native/fs/experiments/qualify.py --extension-dir /tmp/fs-proof --observer /tmp/fs-proof/observe.so
```

The standalone runner needs only stdlib `unittest`. It checks binary data up to
4 MiB, nested Unicode paths, private creation and existing sharing modes,
long filenames, failed replacement cleanup, bounded descriptor counts, and
concurrent reader atomicity.

`observe.c` is a test-only ELF interposition seam. Successful `fsync` operations
still execute against the real filesystem. Isolated child processes record
file/directory sync and rename order, inject first-write `EINTR` and short
writes, inject file-sync failure, and distinguish strict preflight failure from
post-commit failure. Best-effort directory-sync failure keeps legacy success.
The observer must never be loaded during performance measurements.
