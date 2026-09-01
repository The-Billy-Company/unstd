# `unstd.audio`

Part of [`unstd`](../README.md). A stdlib-`wave`-faithful **replacement**
that hands back sample _arrays_ instead of raw frame bytes — the shape a voice
path (TTS/STT) actually wants. The stdlib `wave` module only exposes packed
PCM byte strings; every call site then re-rolls the same `struct`/`array`
unpacking. `unstd.audio` folds that decode into the reader and the encode into the
writer.

## Modules

| File      | Role                                                                                                            |
| --------- | ----------------------------------------------------------------------------------------------------------------- |
| `wavx.py` | `read_wav`/`write_wav` (whole-file) · `read_blocks`/`WavWriter` (streaming) · `wrap_pcm` — numpy-native WAV samples ↔ file. |

```python
from unstd.audio import wavx

samples, sr = wavx.read_wav("in.wav")  # float32 in [-1, 1], sr = Hz
wavx.write_wav("out.wav", samples, sr)  # PCM_16 by default
ints, sr = wavx.read_wav("in.wav", dtype="int16")  # raw signed 16-bit

# streaming — for a live TTS/STT pipeline, not a file already fully in memory:
with wavx.WavWriter("out.wav", sr, channels=1) as w:
    for chunk in synth_chunks:
        w.write(chunk)
for block in wavx.read_blocks("in.wav", blocksize=4096):
    play(block)

wavx.wrap_pcm(raw_pcm_bytes, sr, channels=1, width=2)  # header only, no decode
```

Mono is 1-D `(frames,)`; multichannel is 2-D `(frames, channels)` — the shape
libsndfile/`soundfile` use. Float samples written are clamped to `[-1, 1]` then
quantized; integer samples are written as-is.

## Backend

The `audio` extra (`pip install 'unstd[audio]'`) provides `soundfile==…` +
`numpy==2.5.0`, so reads/writes ride **libsndfile** and samples are
`numpy.ndarray`. Without it there are two guarded stdlib fallbacks:

| Path                    | When                         | Samples             | Read widths | Write subtypes    |
| ----------------------- | ---------------------------- | ------------------- | ----------- | ----------------- |
| `soundfile` + `numpy`   | `audio` extra installed      | `ndarray`           | libsndfile  | libsndfile        |
| stdlib `wave` + `numpy` | numpy present, no soundfile  | `ndarray`           | 8/16/32-bit | `PCM_16`/`PCM_32` |
| stdlib `wave` + `array` | no numpy (pure-stdlib floor) | `list`/`list[list]` | 16-bit      | `PCM_16`          |

The pure-stdlib floor deliberately trades ergonomics for zero dependencies:
16-bit PCM only, Python lists, no vectorization. Wider widths / other subtypes
raise a clear error pointing at the `audio` extra rather than silently degrading.

**Fixed-point scaling is identical on every path** so a file written by one reads
back consistently on another: a _w_-bit sample ↔ float via `± 2**(w-1)`, so
`float32 → PCM16 → float32` round-trips lossless to within one quantization step
(`1/32768`).

## Streaming — `WavWriter` / `read_blocks`

`read_wav`/`write_wav` buffer the whole file; a live pipeline (streaming TTS
synthesis, STT playback) wants frames as they arrive instead. `WavWriter` is a
context manager — `channels`/`subtype` are fixed up front (a stream has no
complete array to infer them from), and each `.write(chunk)` call appends one
chunk; `read_blocks(path, blocksize)` is the read-side generator, `blocksize`
frames at a time. Fast path: `soundfile`'s own `SoundFile`/`blocks` streaming
API. Fallback: stdlib `wave`'s `writeframesraw`/chunked `readframes`, which
requires the write target be **seekable** (a real file path, or a file object
opened on one) so `close()` can patch the RIFF/`data` sizes — a genuinely
non-seekable sink (a raw socket) needs the `audio` extra.

## `wrap_pcm` — a header, nothing else

Some PCM never becomes samples: bytes straight off a wire (a codec's output, a
socket frame, a transcription upload) just need a RIFF/WAVE container around
them. `wrap_pcm(raw, samplerate, channels=1, width=2)` does exactly that — no
scale, no clamp, no requantize — where routing the same bytes through
`write_wav` would decode to an array and re-quantize back, paying two
conversions and a quantization step to reproduce its own input.

## Prior art

- **[`soundfile`](https://github.com/bastibe/python-soundfile)** (Bastian
  Bechtold) — Python bindings over **[libsndfile](https://libsndfile.github.io/libsndfile/)**
  (Erik de Castro Lopo), the fast path this module wraps.
- The stdlib [`wave`](https://docs.python.org/3/library/wave.html) module — the
  raw-frame surface `unstd.audio` replaces, and the fallback codec.
