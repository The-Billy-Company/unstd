# WAV regressions

`test_integer_pcm.py` retains integer sample-count scaling and clipping cases, using stdlib `wave`/`struct` to inspect output independently. `test_partial_frames.py` retains a deterministic fuzz case whose incomplete interleaved frame previously produced incidental indexing or reshape errors.

Array, NumPy, and soundfile cases run where supported. Partial-frame checks force fallback readers and require the documented `ValueError` for both one-shot and block reads.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/regression -q
```

See [the module test overview](../README.md) for related evidence.
