# Dataset offset kernels

The optional CPython extension maps immutable files and copies indexed JSON
rows from snapshot buffers. [`src/adapter.c`](src/adapter.c) owns byte-order conversion, geometry
and row checks, bounded copying, and periodic signal handling. JSON decoding
stays in `unstd.serde`; Python owns publication and selects the native or
portable mapping owner.

Each entry checks the format magic, header count/index, exact table size and
endpoints. Selected rows require strictly increasing in-range offsets and a
newline terminator, with no physical newline inside the JSON payload. That
framing check prevents batch decoding from splitting one indexed row into
multiple records. `validate` checks every row; `span` checks every selected
row before copying its contiguous range. Buffers remain acquired and the GIL
stays held through all pointer use. Signal callbacks run before rechecking a
row, so mutable-buffer changes cannot create unchecked memory access.

`map(fd)` uses a read-only private POSIX mapping after a regular-file and size
check, without synchronizing an already published immutable file again. The
mapping retains the inode after the caller closes its descriptor. It exposes
readonly buffers and refuses `close()` while any are exported; views own a
strong reference. Integer/slice reads copy bytes and `find` accepts one byte.
Argument conversion finishes before checking that the mapping remains open.
The standard `mmap.__new__` audit event precedes final descriptor validation.
Windows returns `None`, selecting Python's standard mapping implementation.

The Python storage implementation is the portable reference. The native module
is private, has `KERNEL_VERSION = 2`, and adds no dependency or format variation.

```sh
uv sync --reinstall-package unstd
python -m pytest tests/suites/product/dataset -q
```
