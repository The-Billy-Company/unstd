---
doc_radar:
  sentinels:
    - file: wavx.py
      contains: ['from unstd.audio.pcm import']
    - file: pcm.py
      contains: ['def _check_pcm_frames(']
---

# `unstd.audio`

Part of [`unstd`](../README.md). PCM buffer conversion and WAV readers/writers
that expose decoded samples instead of `wave`'s packed frame bytes. Whole-file,
streaming and raw decoding APIs use NumPy arrays when available and Python
lists on the dependency-free fallback.

## Modules

| File      | Role                                                                                                            |
| --------- | ----------------------------------------------------------------------------------------------------------------- |
| `wavx.py` | `wrap_pcm`/`decode_pcm` (raw buffers) · `read`/`write` (whole-file) · `blocks`/`Writer` (streaming). |

```python
from unstd.audio import wavx

samples, sr = wavx.read("in.wav")  # float32; PCM normalized to [-1, 1], sr = Hz
wavx.write("out.wav", samples, sr)  # PCM_16 by default
ints, sr = wavx.read("in.wav", dtype="int16")  # raw signed 16-bit

# streaming — for a live TTS/STT pipeline, not a file already fully in memory:
with wavx.Writer("out.wav", sr, channels=1) as w:
    for chunk in synth_chunks:
        w.write(chunk)
for block in wavx.blocks("in.wav", blocksize=4096):
    play(block)

wavx.wrap_pcm(raw_pcm_bytes, sr, channels=1, width=2)  # header only, no decode
samples = wavx.decode_pcm(raw_pcm_bytes)  # PCM16 → owned, writable float32
```

Mono is 1-D `(frames,)`; multichannel is 2-D `(frames, channels)` — the shape
libsndfile/`soundfile` use. For `PCM_16` and `PCM_32`, float samples are clamped
to `[-1, 1]` and quantized; integer inputs are raw sample counts,
clipped to the destination width rather than rescaled from their source dtype.
Soundfile's `FLOAT`/`DOUBLE` output preserves floating values outside that range.

## Backend

The `audio` extra (`pip install 'unstd[audio]'`) provides `soundfile>=0.14` +
`numpy>=2.5`, so reads/writes ride **libsndfile** and samples are
`numpy.ndarray`. Without it there are two guarded stdlib fallbacks:

| Path                    | When                         | Samples             | Read widths | Write subtypes    |
| ----------------------- | ---------------------------- | ------------------- | ----------- | ----------------- |
| `soundfile` + `numpy`   | `audio` extra installed      | `ndarray`           | libsndfile  | libsndfile        |
| stdlib `wave` + `numpy` | numpy present, no soundfile  | `ndarray`           | 8/16/32-bit | `PCM_16`/`PCM_32` |
| stdlib `wave` + `array` | no numpy (pure-stdlib floor) | `list`/`list[list]` | 16-bit      | `PCM_16`          |

Both fallback readers decode integer PCM only. IEEE float WAV files require
the `audio` extra; fallbacks raise `wave.Error`, including on Python 3.15 where
stdlib `wave` can expose those floating-point frames.

The pure-stdlib floor deliberately trades ergonomics for zero dependencies:
16-bit PCM only, Python lists, no vectorization. Wider widths / other subtypes
raise a clear error pointing at the `audio` extra rather than silently degrading.

PCM normalization uses `2**(w-1)` as the scale for a _w_-bit sample. Floating
quantization can differ by a quantization step between backends; callers should
not rely on byte-identical float-to-PCM output. `PCM_16` has a normalized step
of `1/32768`.

On the NumPy fallback, reading PCM16 with `dtype="int16"` copies the existing
sample counts directly into independent, writable arrays. It preserves their
little-endian dtype and channel shape while avoiding an unnecessary float
conversion. `blocks` gives each yielded chunk its own storage.

## Streaming — `Writer` / `blocks`

`read`/`write` buffer the whole file; a live pipeline (streaming TTS
synthesis, STT playback) wants frames as they arrive instead. `Writer` is a
context manager — `channels`/`subtype` are fixed up front (a stream has no
complete array to infer them from), and each `.write(chunk)` call appends one
chunk; `blocks(path, blocksize)` is the read-side generator, `blocksize`
frames at a time. Fast path: `soundfile`'s own `SoundFile`/`blocks` streaming
API. Fallback: stdlib `wave`'s `writeframesraw`/chunked `readframes`. Use a
seekable output so the writer can update RIFF/`data` sizes when closing. The
soundfile backend also needs seek/tell support for WAV output; installing the
extra does not make a raw socket a supported WAV sink.

## `wrap_pcm` — a header, nothing else

Some PCM never becomes samples: bytes straight off a wire (a codec's output, a
socket frame, a transcription upload) just need a RIFF/WAVE container around
them. `wrap_pcm(raw, samplerate, channels=1, width=2)` does exactly that — no
scale, no clamp, no requantize — where routing the same bytes through
`write` would decode to an array and re-quantize back, paying two
conversions and a quantization step to reproduce its own input.

Pass accumulated `bytearray` buffers directly: `wrap_pcm(pcm, sr)` avoids the
intermediate full copy in `wrap_pcm(bytes(pcm), sr)`. C-contiguous buffers and
typed memoryviews are accepted; header sizes count bytes rather than elements.
The returned immutable bytes own their storage. Noncontiguous views raise
`BufferError`. This remains a header-only operation; frame alignment is the
caller's responsibility.

## `decode_pcm` — raw voice frames to samples

`decode_pcm(raw, *, channels=1, width=2, dtype="float32")` decodes interleaved
little-endian PCM directly. The sample rate stays with the caller. Defaults
decode mono PCM16 voice frames; widths 1/2/4 represent unsigned PCM8 and
signed PCM16/32. Output dtypes and channel shapes match `read`. PCM16 is
available as Python lists without dependencies; NumPy adds PCM8/32 and arrays,
without requiring soundfile. Arrays own writable storage and never alias the
source or another decoded chunk. Positive integer channels and complete frames
are required; noncontiguous buffers raise `BufferError`.

Floating output casts directly to the requested dtype and scales in place,
with one output array instead of full intermediate arrays. PCM32-to-int16 conversion retains
float64 precision before rounding. This conversion does not resample, downmix,
retain a rolling buffer, or apply voice activity policy.

Use `wrap_pcm` for sound observations, batch STT uploads and narration.
Live voice pipelines can separately normalize raw PCM16 for feature extraction,
rolling retention, diarized STT and turn inference. The
[audio reports](../../../bench/reports/audio/README.md) retain consumer audits,
adoption examples, timings and allocation bounds.

## Prior art

- **[`soundfile`](https://github.com/bastibe/python-soundfile)** (Bastian
  Bechtold) — Python bindings over **[libsndfile](https://libsndfile.github.io/libsndfile/)**
  (Erik de Castro Lopo), the fast path this module wraps.
- The stdlib [`wave`](https://docs.python.org/3/library/wave.html) module — the
  raw-frame surface `unstd.audio` replaces, and the fallback codec.

## Source ownership

This package is one cohesive code leaf. [`wavx.py`](wavx.py) owns WAV I/O,
backend selection, public types and streaming writers. [`pcm.py`](pcm.py) owns
contiguous-buffer normalization, scalar/frame validation, integer clipping,
channel checks and stdlib PCM16 conversion without NumPy or soundfile.
`wavx` imports these conversion routines directly; callers continue to use
`from unstd.audio import wavx`.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Package entry point and usage contract |
| [`wavx.py`](wavx.py) | Public WAV and raw PCM operations |
| [`pcm.py`](pcm.py) | Sample conversion and frame validation |

Run the contracts from the repository root with
`uv run --no-sync python -m pytest tests/suites/product/audio -q`.
