# TOML stream integration evidence

`test_streams.py` reads real binary files (including CRLF and non-UTF-8 content) and rejects text handles with stdlib's binary-mode error. Fixture-owned streams fail on read or write and record each call. Assertions check that the original exception object propagates, that `dump` renders the whole document before its single write, that unrepresentable values fail before the stream is touched, and that a binary sink receives nothing. A fresh stream must then succeed. Real text files written by `dump` must read back through `load`.

Read cases need no extra. Writer cases require tomlkit from the `toml` extra; under `--require-extras` a missing backend fails instead of skipping.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/toml/integration -q
```

See [the module test overview](../README.md) for related evidence.
