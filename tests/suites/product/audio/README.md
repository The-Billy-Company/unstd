# WAV audio contracts

Contracts for `unstd.audio.wavx`: sample encoding, decoding, streaming, and PCM framing across available backends.

- [unit/](unit/README.md): WAV API behavior and backend interoperability.
- [properties/](properties/README.md): generated quantization, shape, and clamping laws.
- [regression/](regression/README.md): integer PCM scaling and incomplete-frame defects.

The array fallback runs without audio extras. NumPy and soundfile cases run when available; the property leaf requires NumPy.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/audio -q
```

See [the module documentation](../../../../src/unstd/audio/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
