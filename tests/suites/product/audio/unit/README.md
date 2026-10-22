# WAV API contracts

`test_audio.py` synthesizes WAV fixtures and compares sample values, headers, channel layout, and stream behavior with stdlib `wave`. It covers file-like handle ownership, `wrap_pcm`, block reads, streamed writes, unsupported formats, and invalid dtypes.

`test_pcm_quantization.py` derives expected PCM codes with exact fractions: nearest-code rounding off the grid, half-step ties, full-scale endpoints and over-range clipping, and exact 8/16/32-bit decoding. It cross-reads PCM16 between backends and pins the soundfile-only 24-bit and IEEE-float capability against fallback refusals. soundfile may differ by one quantization step, as documented.

`test_layouts.py` checks 3- and 6-channel interleave order through stdlib `wave` bytes and zero-frame files across `write`, `Writer`, `read`, and `blocks`.

The fixture always forces the pure-stdlib array implementation, adds the NumPy fallback when available, and exercises soundfile when installed.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/unit -q
```

See [the module test overview](../README.md) for related evidence.
