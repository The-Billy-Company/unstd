# WAV quantization properties

`test_wav_quant.py` generates float samples and checks PCM16 error within half a quantization step, stereo shape/channel preservation, and saturation rather than wraparound for out-of-range values.

NumPy is required. The tests force the NumPy/stdlib-wave fallback even when soundfile is installed; they do not measure soundfile quantization.

`test_stream_chunks.py` generates int16 frames, channel counts, `Writer` chunk partitions and `blocks` sizes on every available backend. Streamed files must carry exactly the hand-packed frames, and blocks must partition them.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/properties -q
```

See [the module test overview](../README.md) for related evidence.
