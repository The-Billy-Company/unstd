# Mathematical backend implementation and qualification

October 22, 2026. This follows the [baseline audit](report.md); its raw evidence
and original source identities are preserved separately. The audit covers 221
exported callable entries, 18 packages and 226 registered benchmark workloads.

## Implemented production changes

PCM16-to-int16 reads in the NumPy fallback now make one owned, writable copy.
They avoid the previous float64 conversion, normalization, clipping and rounding.
Frame validation, channel shape, little-endian sample identity and output
ownership are preserved. PCM8/PCM32 and float outputs retain their conversion
contracts. Exhaustive PCM8/PCM16 values, 100,020 PCM32 boundary/random values,
malformed input, empty input and complete streaming lifetimes were checked.

[67 paired complete public calls](audio-implementation-results.json) retain
seven alternating wall/current-thread CPU pairs and exact output checks. For
PCM16/int16 fallback reads, median CPU speedups were 2.02x at one sample, 1.98x
at 16, 2.29x at 1,024 and 10.50x at 65,536. Consumed blocks improved 3.77x at
65 samples and 5.48x at 4,096. This optimization applies to the NumPy fallback;
the usual SoundFile route has its own binary qualification below. Unchanged
controls retain their timing variation instead of being reported as wins.

`rand.Generator` initializes its stdlib generator once, removing a discarded
entropy seed for exact base-class construction. Default seeded histories and
subclass seed hooks are preserved. Explicit `backend="numpy"` callers may
select `bit_generator="PCG64DXSM"`, `"SFC64"`, `"Philox"` or `"PCG64"`.
Reseeding preserves that selection. Defaults remain stdlib/PCG64. These are
simulation engines and do not replace OS-backed secrets.

[56 paired workloads](random-implementation-results.json) measured seeded
constructor CPU speedups of 4.48x for stdlib and 2.65x for NumPy. Default reseed
controls were mixed/noisy (0.999x and 0.983x CPU, respectively); no universal
reseed gain is claimed. Philox reseeding was 0.90x against the different legacy
PCG64 engine, an explicit-selection tradeoff. All 15 hot draw instruction
sequences, three instance slots and 56-byte shallow instance size are unchanged.
See [full evidence and reproduction](random-implementation.md).

UUID fallback documentation now accurately distinguishes its 12-bit counter
from CPython's 42-bit UUIDv7 counter. UUID generation and persisted identities
are unchanged.

## Patched audio backend

The [authenticated builder](../../../tests/tooling/runtime/audio_build.py) applies
the official libsndfile parser fix to verified 1.2.2 sources. It produces a
host-qualified SoundFile wheel, exact source/backport/compiler/library
provenance and optional trusted same-job policy. Codecs remain available;
static archives are privately staged before hashing/linking. The external
[admission tool](../../../tests/tooling/runtime/audio.py) resolves SoundFile's
actual CFFI function through the platform loader, hashes its library file and
requires separately trusted provenance. Matching version strings alone fail.
No production audio call imports or executes these checks.

The concrete macOS arm64 artifact is:

| Artifact | SHA256 |
| --- | --- |
| Provenance | `43e14a7dc67c0b0b9030b2fe2164e43e8a4c29d39733449d3da463657c9e97fd` |
| SoundFile wheel | `75c46a5a66d6d7cfd0b3c6d60346a1dd83fc3e0bd26cf3398bf136282c59ed95` |
| Native library | `e65caa96729c3602a8fe6567bdb7cfd25db826302100396019088cc4c8ceae93` |

The wheel/source/build output is retained in `.local/audio-patched-474e4d3-r4`.
The [receipt](sndfile-build-provenance.json) and exact measured
[builder](sndfile-build-measured-source.txt)/[admission](sndfile-admission-measured-source.txt)
snapshots preserve the artifact's original recipe. Later tooling adds private
static-archive staging and stricter malformed-provenance validation; those
changes are not retroactively attributed to that historical receipt.

All 26 formats and subtype sets match the stock library; actual FLAC, Vorbis,
Opus and MP3 roundtrips pass. Linkage retains one libsndfile DSO plus libSystem.
The native library is 2,290,672 bytes versus 3,048,520 bytes originally (24.9%
smaller). [70 exact-result paired public read/write/block comparisons](sndfile-implementation-results.json)
and a [repeat](sndfile-implementation-repeat.json) retain every sample.
The first run's tiny-call CPU ratios reached 1.075; none exceeded 1.03 in the
repeat. A [final exclusive run](sndfile-implementation-final.json) retained one
borderline empty PCM8/float64 ratio of 1.051; the other 69 were below 1.03.
A [longer 21-pair follow-up](sndfile-empty-qualification.json) measured 1.004 CPU
for that case, while an unchanged original-library control measured 1.038.
That follow-up overlapped a local build and its wall clocks remain noisy.
Large conversion calls improved substantially; already-direct int16 reads/blocks
stayed near parity. These observations do not establish a persistent CPU penalty
or prove every input, codec, startup or platform has zero overhead. The repeat briefly
overlapped test collection; it is supplementary evidence, not an exclusive
performance-worker result.

One attempted final run crossed a clock-second boundary: baseline FLOAT WAV
writes themselves differed in their PEAK creation timestamp. The final harness
validates and normalizes only those four timestamp bytes outside timed loops;
all sample, peak magnitude/position and remaining container bytes still match.
[Source/provenance mappings](sndfile-measurement-provenance.json) retain the
original and final drivers. [Nine fresh-process startup pairs](sndfile-startup-results.json)
measured candidate/baseline medians of 0.976 import CPU, 0.936 first-read CPU
and 1.000 peak RSS with warm filesystem caches. Cold storage/page-fault behavior
is not established.

The patched wheel is installed and admitted in both the external qualification
environment and this checkout's `.venv`. The workspace editable package metadata
was refreshed to 3.1.4 and the default native extensions rebuilt.

The required [audio-security workflow](../../../.github/workflows/audio-security.yml)
builds pinned inputs, installs its hash-bound wheel, admits the actual loaded
library and runs audio contracts. It retains evidence and never publishes.
Linux shared-codec builds are explicit host reference artifacts; this macOS
session cannot execute that GitHub runner. Windows build qualification remains.
Downstream applications must provision and admit their exact binary. A normal
registry SoundFile requirement does not guarantee the native fix, and a later
`uv sync` can restore the registry wheel. See [provisioning instructions and
trust limits](../../../native/audio/README.md). The repository's generic allowlist
stays empty; the reviewed local artifact uses its separate trusted policy.

## Compression experiments and retained providers

The local zlib-rs build option uses the current ABI, checked Python allocators,
private symbols and SHA256-verified private archive copies. There is no Cargo
resolution or runtime dispatch. Release-wheel builds reject this experiment
until its complete source/license/SBOM closure is qualified.

[Earlier alternating native comparisons](compression-implementation-results.json)
found zlib-rs full inflation around 1.28x faster but bounded inflation around
0.57x and streaming around 0.91–0.95x baseline speed. Its extension was about
1.60 MB versus 77 KB. These measured costs disqualify it as the default.
The final exact-1.25 default and rs+1.25 artifacts each passed 469 compression
tests; [their final identities](compression-final-artifacts.json) are distinct
from the earlier measured builds. Another 202 build/artifact/supply tests passed.
An earlier Linux ARM64 default build passed 465 tests with one optional skip.

Libdeflate 1.26 is available through an [isolated qualification builder](../../../native/compress/experiments/qualify_libdeflate.py)
that authenticates and stages its reviewed 24-file closure. Default vendored
1.25 source, source manifests and compiler settings are unchanged: 1.26 adds
first-use Linux ARM64 sysfs open/read operations. It has no advertised security
fix to justify that added setup under the requested constraint. See [build
instructions](../../../native/compress/README.md).

The current BLAKE3 worker threshold was rechecked at 1/2/4 MiB in raw, keyed and
derive-key modes with nine alternating pairs and total-process CPU clocks.
[Serial operation](hash-threshold-qualification.json) used less CPU but took
2.26–4.96x the wall time of the existing parallel path. Its policy is retained;
lower CPU consumption alone would hide a throughput regression.

All other per-operation providers retain the audit's decisions. No precision,
metric, byte-order, wire-format, cryptographic identity, buffer-view or default
random-stream substitution was introduced. Existing benchmark ceilings are
unchanged. The work adds no runtime dependency, wrapper, thread pool, global
cache or per-call security check.

## Validation

Final repository checks and integrated suite results are recorded in
[implementation-validation.json](implementation-validation.json). The full suite
passed **10,162 tests, with 93 skipped and 269 deselected**, using the patched audio wheel
in an external locked environment: the original interpreter's stdlib was stored
inside this repository and therefore correctly failed pristine-consumer path
isolation assertions. That environment issue was corrected without weakening
the assertions. Workflow tests also now locate the canonical lock restore by
identity and execute the aggregate gate with the current test interpreter.

No universal no-overhead guarantee is established by these measurements. Changes
with observed material overhead remain explicit experiments; security admission
runs during provisioning, outside mathematical operations.
