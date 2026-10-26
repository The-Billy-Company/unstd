# Buffer selection for the next release

`unpack_array` and `unpack_array_portable` now accept keyword-only element
`count=-1` and byte `offset=0`. This exposes buffer selection through the
existing APIs, without introducing a packet schema or another public module.
NumPy produces views of the original buffer; portable decoding uses compiled
`Struct.unpack_from` and returns owned Python scalars. Existing calls keep
their conversion, dtype, ownership, and whole-buffer behavior.

Billy has not been edited. This work prepares the library release and qualifies
the proposed caller changes against captured application code.

## Where to adopt it

The strongest live matches are numeric regions inside ColBERT responses:

- `core/foundation/llm/local/colbert.py`, `_remote_index`: reads an `<i8`
  boundary table followed by `<f4` token rows. The current body slice copies
  the full token payload before creating a NumPy view.
- `core/foundation/llm/local/remote.py`, `_validate`: does the same for a
  variable-length boundary table, then applies the existing `TokenIndex`
  admission and immutable snapshots.
- `services/speaker/speaker_plane/core/audio.py`, `decode`: can select its
  existing whole-frame prefix with `count=usable // itemsize` instead of
  slicing. Keep truncation, sample conversion, and downmixing in the speaker
  code. An already aligned exact-bytes full slice is already cheap; no audio
  workflow speedup is claimed here.

The first two paths can use the same generic selection pattern, with the
existing boundary size, dimension, and constructor left in the application:

```python
from unstd.pack import binpack

tokens = np.asarray(
    binpack.unpack_array("<f4", body, offset=boundary_bytes),
    dtype=np.float32,
).reshape(-1, dimension)
offsets = np.asarray(
    binpack.unpack_array("<i8", body, count=boundary_bytes // 8),
    dtype=np.int64,
)
```

Explicit NumPy dtypes preserve the caller's static types and handle the
decoder's list fallback correctly. With the normal NumPy backend, `asarray`
retains the existing array without copying its data. NumPy already implements
the native buffer selection; the speedup comes from removing caller slices,
not from replacing its numerical kernel.

The read-only audit also identified paths that should retain their current
implementation:

- Big-endian 256-double embedding records in `embedding/corpus.py` and
  `embedding/retained.py` already use compiled `Struct` operations. Short local
  probes found nested-list NumPy packing and owned-row decoding slower. A new
  batch wrapper would add abstraction without a demonstrated win.
- Whole-corpus half decoding in `util/linalg/embedding_codec.py` already batches
  compact words into one matrix. Its base64, native/legacy preference, and
  dimensional validation remain application policy; normalization belongs to
  the calling embedding layer. The
  list-only projection can use the existing portable decoder after payload
  selection; measured benefit at 256 values was modest.
- Immutable array snapshots already require owned byte storage. Replacing
  `frombuffer(array.tobytes())` with a shallow wrapper alone does not remove
  that requirement. The benchmark preserves those copies.
- The earlier `engine/bridges/external/_common.py:f32_blob_to_f64` helper has no
  production callers in the inspected Python tree. Its roughly 4× local
  owned-list alternative is not evidence of a live application speedup.

Paths under `core/` above are relative to `services/ai/` in the inspected Billy
checkout. [Provenance](consumer-provenance.json) records the source and pinned
configuration hashes used for the captured ColBERT case.

## Captured consumer results

The [harness](../../../profiles/pack_consumers.py) captures the exact
`TokenIndex` class and `_remote_index` function, then rewrites exactly two
reviewed sliced `frombuffer` calls. The candidate includes the `asarray`
adaptation above. All remaining statements, shape checks, publication budgets,
boundary/finite-value admission, and defensive immutable snapshots remain
unchanged. Five malformed/adverse payloads must raise the same errors before
and after the rewrite.

Nine alternating paired rounds measure complete decoder calls separately from
array selection alone. Output allocation and reference-count cleanup are timed;
fixture construction and independent struct-derived byte/value checks are not.
GC is disabled only during timed batches. The fixture sizes are bounded
workload scenarios using the real width of 128, not production traffic samples.
Transport, inference, and GPU publication are outside this measurement.

| Token rows × width | Payload | macOS complete decoder, before → after | Speedup | Linux complete decoder, before → after | Speedup |
| --- | --- | --- | --- | --- | --- |
| 128 × 128 | 64 KiB + header | 8.819 → 6.939 µs | 1.27× | 10.974 → 9.528 µs | 1.15× |
| 8,192 × 128 | 4 MiB + header | 185.656 → 126.569 µs | 1.47× | 1962.661 → 168.211 µs | 11.67× |
| 65,536 × 128 | 32 MiB + header | 1669.990 → 1082.526 µs | 1.54× | 15176.764 → 7739.611 µs | 1.96× |

The Linux 4 MiB result varies strongly with the measured allocation/runtime
environment. A [longer repeat](linux-focused-py314.json) with 100 ms target
batches measured **9.77×**, while the 32 MiB complete decoder measured **1.66×**.
Both runs are retained; these are observations on that runtime, not a general
Linux latency guarantee.

Selecting the array views alone improves from 525.090 → 0.740 µs on macOS
(709×) and 7173.611 → 0.740 µs on Linux (9,688×) for the largest payload.
Those results exclude admission and immutable snapshots and must not be used
as a claim of 100× for the complete decoder or application request.

Raw evidence: [macOS](macos-py314.json), [Linux](linux-py314.json), and the
[Linux focused repeat](linux-focused-py314.json). macOS runs on arm64 with
CPython 3.14.9; Linux runs on LinuxKit aarch64/glibc 2.41 with CPython 3.14.8 in
a dedicated Docker Desktop container on the same Mac. Both use NumPy 2.5.3.
This does not qualify bare-metal or x86-64 Linux.

## Release checks and replay

- [macOS product/API/harness/fuzz checks](macos-tests.txt) and
  [Linux checks](linux-tests.txt) cover existing behavior and the extended API.
  The final runs pass **1,328** and **1,272** selected checks, respectively.
  Seventy-seven new product cases exercise ranges, protocol conversion,
  typed/scalar/multidimensional exporters, ownership, and invalid bounds.
- [Wheel and sdist checks](packaging-tests.txt) pass all six selected tests:
  pristine base consumers, isolated NumPy-only consumers, and strict downstream
  typing with negative controls, through both build routes. The pack Python
  sources inside both built wheels match the measured source.
- With NumPy imports blocked, [macOS](macos-base-tests.txt) and
  [Linux](linux-base-tests.txt) each pass 418 product tests, with 698
  NumPy-dependent cases skipped.
- [Python 3.12/3.13 smoke checks](portable-python-versions.txt) each pass 152
  portable range, dtype, introspection, and oversized-bound checks with site
  packages disabled.
- Fuzz envelopes now check selected windows against an independent struct
  oracle and reject out-of-buffer windows. An injected decoder that ignores
  byte offsets must fail the oracle. Ruff check/format and strict mypy pass for
  the changed library and qualification code.

Captured application definitions remain in ignored `.local/` storage, outside
release inputs. Regenerate that private capture from the Billy checkout before
replay; its digest must match the retained provenance for these exact results:

```console
python bench/profiles/pack_consumers.py --billy-root ../billy --write-capture .local/pack-consumer-capture.json
python bench/profiles/pack_consumers.py --capture .local/pack-consumer-capture.json --source src --rounds 9 --batch-ms 50 --output /tmp/pack-consumers.json
python -m pytest tests/suites/product/pack tests/suites/assurance/gates/bench/test_pack_consumers.py -q
```

[Receipt](receipt.json) binds the implementation, harness, provenance, evidence,
source archives, and built artifact hashes. Release notes live in
[the 4.0.0 changelog](../../../../CHANGELOG.md#400---2026-10-25).
