# WAV container boundaries

`test_riff_boundaries.py` feeds hand-built RIFF/WAVE containers to `read` and `blocks`. Malformed magic, form type, missing or misordered chunks, short or unsupported `fmt `, zero channels or bits, and overrunning chunks must fail exactly as the selected codec fails on the same bytes: stdlib `wave` for the fallbacks, `soundfile.read` for the fast path. Caller sources stay open. Padded odd-sized chunks, chunks after `data`, and whole-frame truncation decode to the hand-packed samples.

Array, NumPy, and soundfile cases run where available.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio/security -q
```

See [the module test overview](../README.md) for related evidence.
