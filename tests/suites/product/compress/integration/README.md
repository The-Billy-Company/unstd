# Gzip stream faults and release

`test_stream_faults.py` fails caller sinks and sources at every counted call, and files `gzipx` opens on any write, on read, or on close, in binary and text modes. The application's exception object must reach the caller, caller streams stay open, gzip files end closed, and owned handles are released. Descriptor counts cover partial reads closed early, failed text-wrapper setup, and corrupt owned files.

Descriptor checks need `/proc/self/fd` or `/dev/fd`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/compress/integration -q
```

See [the module test overview](../README.md) for related evidence.
