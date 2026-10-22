# WAV API contracts

`test_audio.py` synthesizes WAV fixtures and compares sample values, headers, channel layout, and stream behavior with stdlib `wave`. It covers file-like handle ownership, `wrap_pcm`, block reads, streamed writes, unsupported formats, and invalid dtypes.

The fixture always forces the pure-stdlib array implementation, adds the NumPy fallback when available, and exercises soundfile when installed.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/unit -q
```

See [the module test overview](../README.md) for related evidence.
