# Gzip adapter and inflater

[`adapter.c`](adapter.c) owns the module, libdeflate buffer decoder and CRC32.
The ISIZE allocation hint is capped at 64 MiB and by a compressed-size expansion
bound. Outputs below 64 KiB reuse an interpreter-local decoder with the GIL held;
outputs of at least 64 KiB use a per-call decoder and release the GIL to decode.

[`stream.c`](stream.c) owns the inflater and retained input. It keeps the GIL,
retains exact `bytes`, copies other buffer exporters, and rechecks state after
exporter callbacks. Output respects `max_length`; negative limits, and zero in
legacy mode, mean unlimited. EOF and unused input feed the Python gzip reader.

[`inflate.c`](inflate.c) exposes the narrow ABI in [`stream.h`](stream.h): codec
state uses checked Python RAW allocators; input/output pointers are borrowed only
for each decode call and cleared afterward. It links platform zlib on macOS or
the prefixed [vendored closure](../vendor/README.md) elsewhere, unless the
[local zlib-rs experiment](../experiments/README.md) is explicitly selected.

All three C files build into `unstd.compress._gzip_native`.
