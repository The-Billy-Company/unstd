# WAV regressions

`test_integer_pcm.py` retains integer sample-count scaling and clipping cases, using stdlib `wave`/`struct` to inspect output independently. `test_partial_frames.py` retains a deterministic fuzz case whose incomplete interleaved frame previously produced incidental indexing or reshape errors. `test_frame_shape.py` retains fallback writes that accepted ragged frames or `Writer` chunks with another channel count. `test_writer_close.py` retains a closed fallback `Writer` that crashed inside `wave` instead of refusing writes.

Array, NumPy, and soundfile cases run where supported. Partial-frame checks force fallback readers and require the documented `ValueError` for both one-shot and block reads.

`test_pcm16_owned_decode.py` checks every PCM16 code through NumPy fallback
whole-file and streaming reads. It pins independent writable array storage,
little-endian dtype, empty/channel shapes and unchanged PCM8/PCM32 quantization.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/regression -q
```

See [the module test overview](../README.md) for related evidence.
