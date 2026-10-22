# Parallel BLAKE3 hashing

This records the initial 16 MiB implementation. The
[follow-up report](crypto-hash-tuning-results.md) measures the current 4 MiB
threshold and faster hex output against this version.

One-shot `digest.raw`, `digest.hex`, `digest.derive_key`, and the MAC helpers
now use fresh BLAKE3 worker pools for exact `bytes` inputs of at least 16 MiB.
The pool uses at most eight workers, capped by the reported CPU count. Smaller
inputs and other accepted buffers follow their original serial calls.

Public signatures, exports, constants, digest values, truncation, and validation
are unchanged. `Stream` and `legacy_blake2b` retain their original implementation.
Streams still consume chunks eagerly and remain usable after a fork.

## Worker lifetime and failures

The backend's automatic setting uses a process-global Rayon pool. A warmed pool
can hang after `fork()`, including when this package is first imported in the
child. Explicit positive worker counts create fresh pools instead. No hasher,
key, context, or material is cached between calls.

If the OS refuses worker creation, the backend currently exposes a PyO3 panic
containing `ThreadPoolBuildError`. Only that failure retries serially. Ordinary
validation errors, interrupts, unrelated panics, and a failed serial retry
propagate. Tests cover both fork import orders and injected worker refusal.

## Measurements and limits

The [recorded run](crypto-hash-results.json) compares the saved original Python
wrappers with the current wrappers, using the same BLAKE3 1.0.10 installation
and identical prepared inputs. It checks outputs, types, public signatures,
exports, and constants before timing. Each of 22 workloads has 25 alternating
batches; GC is disabled during timing. Reports retain every sample, order,
source and harness hashes, versions, minima, paired medians, and noise flags.

The macOS arm64 host ran Python 3.13.14 with 18 logical CPUs. Its one-minute
load average was about 67 when recorded. **21 of 22 workloads were noisy** by
the harness's 25% spread criterion. These results demonstrate local gains on
large inputs, but do not establish a stable performance floor or a universal
speedup. Even the fastest batches can contain scheduling interference.

The table uses the fastest recorded batch on each side, consistent with the
repository's main benchmark estimator. Complete paired medians and losses
remain in the JSON.

| Operation | Input | Original ms | Current ms | Original/current |
| --- | ---: | ---: | ---: | ---: |
| `digest.raw` | 16 MiB | 7.985 | 1.923 | 4.15× |
| `digest.raw` | 64 MiB | 32.291 | 6.217 | 5.19× |
| `digest.hex` | 64 MiB | 52.152 | 6.045 | 8.63× |
| `digest.derive_key` | 64 MiB | 41.514 | 6.844 | 6.07× |
| `token.mac_raw` | 64 MiB | 62.251 | 12.651 | 4.92× |
| `token.mac_hex` | 64 MiB | 33.665 | 6.639 | 5.07× |

The eligibility branch has a small cost on tiny calls: fastest 64-byte raw
hashing rose from 377 ns to 402 ns, and the paired median ratio was 0.93×.
The unchanged 64 MiB stream measured 31.937/31.957 ms in its fastest batches.
Worker creation can outweigh parallelism on smaller buffers; the threshold
keeps those buffers on their existing backend path. Performance near the
crossover depends on CPU count and competing work.

Reproduce with a source snapshot taken before the change, containing
`unstd/crypto` and its private helpers:

```console
.venv/bin/python bench/crypto_hash.py --compare-source /path/to/before/src --rounds 25 --seconds 0.005 --output /tmp/crypto-hash.json
```

## Validation

- Python 3.13.14: 306 crypto, MAC primitive, and optional-collection checks pass.
- Python 3.12.13 with the BLAKE3 1.0.0 dependency floor: 31 new hashing checks pass.
- Python 3.14.6 with BLAKE3 1.0.10: the same 31 checks pass.
- Ruff checks, formatting, and strict mypy pass for the changed Python files.

The new checks compare all three BLAKE3 modes with the serial backend around
16 MiB, preserve buffer and exception behavior, verify concurrent independence,
exercise eager and reusable streams, and run bounded real fork subprocesses.
