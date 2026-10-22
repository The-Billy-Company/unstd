# WAV replay inputs

These version-1 envelopes drive the [WAV target](../../targets/wav.py) and
`unstd.audio.wavx`. Byte 0 modulo three selects generated PCM16, raw RIFF bytes,
or a generated invalid header. Byte 1 selects channels/rate or corruption;
remaining bytes supply PCM frames or the complete raw file. Missing selectors
mean zero, so empty input generates an empty mono PCM16 file.

Cases cover mono/multichannel scaling, streaming/writing, empty files, malformed
RIFF fields, allocation bounds and retained chunk-extent/partial-frame
regressions. Raw admission limits channels and declared allocations before
native reads. Valid, consistent PCM16 compares against stdlib `wave` and exact
sample arithmetic; other admitted RIFF files exercise error handling without
claiming parity for soundfile's broader format support.

`seeds.json` authenticates every complete envelope under the
[corpus rules](../README.md). From the repository root:

```console
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target wav --backend stdlib
```

The installed backend requires `soundfile` and `numpy`; see the
[fuzz guide](../../README.md) for setup, budgets and finding reproduction.
