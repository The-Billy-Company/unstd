# BLAKE3 crossover and hex output tuning

This follows the [initial parallel hashing change](crypto-hash-results.md).
The [current report](crypto-hash-adaptive-results.md) covers the later adaptive
worker counts and native stream batching.
The comparison source is the complete crypto package from `78be815`, with the
16 MiB threshold and backend `hexdigest` calls. The implementation measured here:

- Uses fresh, bounded worker pools from **4 MiB**, retaining the eight-worker
  cap. Smaller inputs and other accepted buffer types keep their serial path.
- Finalizes to bytes and uses Python's `bytes.hex()` for full and truncated hex
  output. Full-width calls also avoid the truncation helper, including on a
  reusable `Stream`.

Algorithms, public signatures, exports, constants, input acceptance, validation
order, truncation, and results remain unchanged. Worker creation failures still
retry serially, and streams retain eager incremental absorption. No messages,
keys, contexts, or backend hashers are cached between calls.

## Paired measurements

The [complete run](crypto-hash-tuning-results.json) contains 41 workloads with
25 alternating batches each, identical prepared inputs, and output/type,
signature, export, and constant checks before timing. It records source and
harness hashes, versions, every observation, minima, paired medians, and noise
flags. Saved private helpers now load in an isolated namespace; a missing
helper fails instead of borrowing the current implementation. Recorded Python
source executes directly so stale bytecode cannot disagree with its hash.

The host ran Python 3.13.14 and BLAKE3 1.0.10 on macOS arm64 with 18 logical
CPUs. The ending one-minute load average was **40.80**. **40 of 41 workloads were
noisy** by the 25% sample-spread criterion. These are local measurements rather
than portable speed guarantees; both estimators and all losses remain in JSON.

Times below are the fastest batch means in microseconds, matching the main
repository harness. The last column independently reports the median of each
round's before/after ratio.

| Operation | Input | Before µs | After µs | Fastest ratio | Paired median ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| `digest.raw` | 4 MiB | 1903.541 | 813.083 | 2.34× | 1.32× |
| `digest.raw` | 8 MiB | 3800.500 | 1376.125 | 2.76× | 1.36× |
| `digest.hex` | 4 MiB | 1932.875 | 781.083 | 2.47× | 1.64× |
| `digest.hex` | 8 MiB | 3808.792 | 1057.834 | 3.60× | 0.86× |
| `digest.derive_key` | 4 MiB | 1908.792 | 1533.500 | 1.24× | 0.43× |
| `digest.derive_key` | 8 MiB | 3811.084 | 1809.584 | 2.11× | 0.80× |
| `token.mac_raw` | 4 MiB | 1912.903 | 616.417 | 3.10× | 0.64× |
| `token.mac_raw` | 8 MiB | 3810.542 | 1206.459 | 3.16× | 1.26× |
| `token.mac_hex` | 4 MiB | 1905.500 | 565.209 | 3.37× | 0.78× |
| `token.mac_hex` | 8 MiB | 3809.042 | 1363.041 | 2.79× | 1.27× |
| `digest.hex` | 64 B | 0.581 | 0.470 | 1.24× | 1.21× |
| `token.mac_hex` | 64 B | 0.692 | 0.570 | 1.21× | 1.20× |
| `Stream.hex` | 64 B | 0.277 | 0.176 | 1.57× | 1.56× |
| `Stream.hex(8)` | 64 B | 0.246 | 0.216 | 1.14× | 1.15× |

Inputs below 4 MiB retain serial hashing because pool setup can outweigh the
work; exploratory runs found 1 MiB unreliable. The raw hashing path at 16/64 MiB
and stream absorption are unchanged. Their measured ratios still vary: the
64 MiB raw case has a 1.01× paired median, while the unchanged 64 MiB stream
measures 0.81×. Newly parallel operations can also lose under scheduling load:
4 MiB key derivation measures 0.43× and 4 MiB raw MAC measures 0.64× by paired
median, despite faster best batches. The lower threshold improves the measured
fastest batches, but does not guarantee lower latency under contention. This
evidence does not establish a performance floor.

Pool reuse was considered but rejected: backend copies create new pools, and
dropping inherited Rayon pools can acquire worker mutexes left behind by a
fork. Fresh pools preserve the existing fork behavior without retained state.
The relevant upstream implementations are
[BLAKE3 Python bindings](https://github.com/oconnor663/blake3-py/blob/1.0.10/src/lib.rs)
and [Rayon worker sleep handling](https://github.com/rayon-rs/rayon/blob/v1.11.0/rayon-core/src/sleep/mod.rs).

Reproduce with a saved source directory containing `unstd/crypto` and all its
private helpers:

```console
.venv/bin/python bench/crypto_hash.py --compare-source /path/to/78be815/src --rounds 25 --seconds 0.005 --output /tmp/crypto-hash-tuning.json
```

## Validation

- Python 3.12.13 with BLAKE3 1.0.0: 198 crypto unit checks pass.
- Python 3.14.6 with BLAKE3 1.0.10: the same 198 checks pass.
- Python 3.13.14: 323 crypto, MAC primitive, optional-collection, and benchmark
  checks cover known answers, the new boundary, accepted buffers, exceptions,
  concurrent independence, real fork subprocesses, and worker-refusal fallback.
  Custom index objects retain the backend's hex-specific overflow errors.
- Ruff checks/formatting and strict mypy pass for the changed Python modules.
