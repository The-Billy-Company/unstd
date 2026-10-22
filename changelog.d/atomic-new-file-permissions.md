New `fs.atomic_write` and `fs.atomic_writer` destinations request owner-only
`0o600` instead of `0o666` before the kernel applies the caller's umask. New
atomic files and their scratch files no longer grant group or world access by
default, even under permissive umasks. A normal `0o022` umask now produces
`0o600`, deliberately stricter than ordinary `open()` creation. Set new-file
sharing explicitly when needed. Rewrites preserve the existing destination mode
exactly, including previously configured group sharing. The library never
changes the process-wide umask.
