# WAV stream failures and release

`test_stream_failures.py` injects scheduled faults: caller sinks and sources fail at every counted call, and files `wavx` opens fail on any write, on read, or on close. Faults must reach the caller as the same exception object, caller streams stay open, and owned handles close. It also covers unseekable sinks (one-shot writes need no seek; a streamed header that needs patching fails loudly) and early `blocks` closure, using descriptor counts.

soundfile reports, rather than raises, exceptions from Python file callbacks, so exception identity runs on the array and NumPy fallbacks. Descriptor and caller-stream checks run on every available backend and need `/proc/self/fd` or `/dev/fd`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/integration -q
```

See [the module test overview](../README.md) for related evidence.
