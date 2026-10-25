Linux CPython builds accelerate ordinary byte and default UTF-8 atomic writes
through a bundled native backend. Missing parents, other codecs, subclasses,
custom bindings, unavailable native capabilities, and optional compiler failure
retain the Python implementation. Native errors never retry the write.
The backend needs no extra or third-party runtime dependency; other platforms,
interpreters and free-threaded builds keep the complete Python path.
Release verification accepts the typing stub on every platform while restricting
the compiled filesystem binary to Linux wheels.

Both paths retain atomic replacement, private scratch creation, mode preservation,
and file/directory syncing. Native cancellation releases acquired resources
before commit and reports committed state afterward. The Python writer now also
identifies a successful rename interrupted before its call returns.
Cleanup failures retain the primary exception and add notes. Linux close is
never retried, and unavailable native entropy declines before scratch creation.

Installed direct wheels and sdist rebuilds exercise native and forced-Python
routes against 13 consumer workload shapes. Linux CPython 3.12–3.14, compiler
failure fallback and sanitizer checks retain their evidence in
[`bench/reports/fs/production/`](../bench/reports/fs/production/README.md).
