# Audio consumers and release changes

The production-source audit found five `wavx.wrap_pcm` calls in two modules,
and no production calls to `read`, `write`, `blocks` or `Writer`.
This release expands the existing wrapper's input type and adds one operation,
`decode_pcm`. No audio dependency, codec, resampler or streaming state machine
is added. The consumer source is unchanged; the adoption examples below apply when
its dependency is updated to a release containing this API.

## Existing uses

Paths below are relative to the consumer repository. The
measurement artifact in this directory retains source text, line locations,
file hashes and symbol hashes for eight relevant consumer functions.

| Consumer | Production source | Input and operation |
| --- | --- | --- |
| Native sound observations | `services/ai/core/domain/media/sound/audio.py:129,144` | Mono PCM16 decoded from an audio/video excerpt, wrapped for OpenRouter or Google native-audio input |
| Narration | Same file, `:322,376` | Obtain WAV header size for the byte budget; collect TTS chunks in a bytearray, then wrap complete narration |
| Batch transcription | `services/ai/transport/speech/elevenlabs/stt.py:631,679` | Wrap PCM16 at the AudioSpec's rate/channel count for multipart upload |
| Live feature ingestion | `services/ai/transport/livekit/sidecar/core/runner.py:950` | Normalize each mono 16 kHz PCM16 chunk before ring storage, speech and tempo processing |
| Voice retention | `services/ai/transport/livekit/worker/pcm_buffer.py:42` | Normalize the same format for a 32-second rolling buffer |
| Offline diarized recognition | `services/ai/transport/livekit/worker/diarized_stt.py:166` | Normalize PCM16 for Whisper |
| Turn inference | `services/ai/transport/livekit/worker/smart_turn_model.py:179` | Normalize PCM16 for endpoint inference in a process worker |
| Python feature fallback | `services/ai/transport/livekit/sidecar/features/sidecar_features.py:281` | Normalize PCM16 before downmix/resampling; keep the existing preferred Rust route |

The shared media decoder normally produces mono 16 kHz `s16le`
(`services/ai/core/domain/sight/render.py:726,998`), with a 12 MiB decoded-output
budget (`:83,972`). Narration includes its WAV header within the same byte
budget and has a 20-second deadline (`audio.py:32,332,344`). The AI and speaker
services already install NumPy but do not request `unstd[audio]`.

Speaker's `services/speaker/speaker_plane/core/audio.py:52` also decodes raw
s16/s32/f32; its WAV branch at `:67` can already use `wavx.read` when its backend
requirements fit. Its raw branch deliberately trims incomplete frames, so a
strict decoder must not replace that policy implicitly. Its `encode_wav`
definition has no production callers and does not justify another API.

## Changes and adoption

Narration can remove one full PCM copy:

```python
# pcm is the existing accumulated bytearray.
wav_bytes = wavx.wrap_pcm(pcm, spec.sample_rate, channels=1)
```

`wrap_pcm` now accepts C-contiguous buffers, including typed memoryviews, and
counts bytes instead of typed elements. Output is independent immutable bytes.
Existing byte inputs keep their header and payload bytes. This operation still
does not interpret samples or validate frame alignment.

Repeated raw normalization can move behind one validated operation:

```python
# Replaces frombuffer(..., dtype="<i2").astype(float32) / 32768.0.
samples = wavx.decode_pcm(pcm_bytes)
```

`decode_pcm(raw, *, channels=1, width=2, dtype="float32")` owns interleave shape,
little-endian interpretation, complete-frame validation, normalization and
independent output storage. It uses NumPy whenever installed, with a PCM16 list
fallback when absent; it does not route raw chunks through soundfile or WAV.
Widths 1/2/4 and the existing float32/float64/int16 decode dtypes share the WAV
decoder's numerical contract. Integer channels must be positive. Mutable sources
are unchanged; results never alias the input or another chunk.

Floating conversion casts once and scales in place. PCM32-to-int16 retains its
float64 rounding path. Existing NumPy WAV reads use the same conversion; the
soundfile route and sample-writing APIs keep their behavior.

Resampling stays with speaker/LiveKit/Rust, μ-law with ElevenLabs' existing LUT,
and rolling retention with its existing owner. These policies do not belong in
a PCM byte conversion API.

## Measured effect

The retained run uses Python 3.14.9 and NumPy 2.5.3 on macOS arm64. Deterministic
PCM16 fixtures contain rails and interior values. Seven adjacent timing pairs
alternate execution order; checks before and after timing require exact output
bytes, dtype, shape, writability and owned storage. Each call produces its
complete output. Traced allocation peaks are measured separately. Source bytes
are hashed before and after the run. The pre-change wrapper is retained in
[`wavx-before.py.txt`](wavx-before.py.txt).

| Operation | Existing call | New call | Traced peak, old → new |
| --- | ---: | ---: | ---: |
| Narration, 32,000 bytes | 1.55 µs | 0.77 µs | 64,187 → 32,154 B |
| Narration, 12 MiB | 408.60 µs | 212.71 µs | 25,166,011 → 12,583,066 B |
| PCM16 normalization, 20 ms | 0.73–0.82 µs | 1.13–1.19 µs | 3,036 → 1,820 B |
| PCM16 normalization, 100 ms | 1.20–1.22 µs | 1.45–1.48 µs | 13,276 → 6,940 B |
| PCM16 normalization, 1 s | 6.09–9.60 µs | 6.18–8.44 µs | 128,476 → 64,540 B |
| PCM16 normalization, 32 s | 184.62–238.46 µs | 167.57–187.20 µs | 4,096,476 → 2,048,540 B |

Narration's existing call includes `bytes(bytearray)` before wrapping; the new
call passes the bytearray directly. At the tiny 640-byte case, this expansion
costs ~0.12 µs despite eliminating the extra copy. Existing byte-input wrapping
is effectively unchanged: 0.223 → 0.246 µs at 640 bytes, 0.543 → 0.564 µs at
32,000 bytes, and 217.2 → 219.7 µs at 12 MiB.

The decoder is primarily a memory, validation and portability improvement.
Validation/dispatch adds ~0.3–0.4 µs for a 20 ms live chunk; long-buffer throughput
is approximately unchanged. This is not evidence of faster model inference,
network transcription or end-to-end voice latency. The two decode controls
retain the existing divide and reciprocal-multiply spellings, which are identical
for PCM16 values. Fixtures and observations are bounded source-based ports;
provider calls and deployed traffic are not measured.

## Verification and regression evidence

Unit/regression checks cover every PCM16 value, PCM8/32 endpoints and rounding,
interleave, empty/channel shapes, partial-frame refusal, all fallback routes,
typed and mutable buffers, and independently readable WAV headers. Benchmark
gates reject output corruption, post-timing drift, borrowed/read-only arrays,
missing/ambiguous source receipts and changed measured source bytes.

The new operation has a workload and measured ceiling in the existing speed
registry. [`decode-speed-addition.json`](decode-speed-addition.json) records
Python 3.13.14/NumPy 2.5.3 on the baseline's CPU/OS, with actual installed
capabilities retained. Only the new row and the reviewed workload hash are
added to the baseline; prior ceilings and evidence are preserved. The
revision-comparison registry omits this operation for releases predating it.

See [README.md](README.md) for paired benchmark requirements. Re-run on the
deployment worker before making absolute throughput claims.

Local validation on this change: the Python 3.14 audio/API/benchmark selection
passed 783 checks (the unrelated full speed-registry selection skips when the
new optional zstd extra is absent); the final focused benchmark gates passed 9.
The audio suite also passed 718 checks on Python 3.13 and 327 on Python 3.12's
base environment, with explicit unavailable-backend skips. Focused strict mypy,
Ruff lint/format and the ten audio workload oracles passed. This is focused
validation, not a claim that every repository or release/CI lane ran locally.

The full-source Pylint duplication scan reported R0801 on two identical
`unstd.yaml.__init__` locations, outside this audio change. Its all-source gate
is therefore not clean in this shared checkout. No unrelated YAML edits or
analyzer policy changes were made for this task.

Full-source Bandit and its report validator passed (8,120 lines scanned). The
audio-only Pylint duplication scan also passed.
