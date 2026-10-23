# Adaptive BLAKE3 workers and native stream batching

This compares the working-tree implementation immediately before this change
with the current implementation. Both use the same installed BLAKE3 backend.
The saved baseline has the previous 4 MiB crossover, eight fresh workers, and
Python stream absorption. The complete sources are identified by SHA-256 in
[the recorded run](crypto-hash-adaptive-results.json).

## Changes

- One-shot hashes, MACs, and key derivation start parallel hashing at 1 MiB,
  with four workers below 4 MiB and eight thereafter, capped by CPU count.
  Pools remain fresh for every call; worker creation refusal still retries
  serially. Accepted non-exact buffers keep their original serial behavior.
- An optional C helper coalesces exact bytes chunks of at most 2 KiB from
  exact lists and tuples containing at least eight elements. Each backend
  update contains at most 64 KiB of combined bytes. Temporary memory remains
  bounded independently of the total input. Large chunks pass through unchanged.
- Mutable buffers and subclasses first flush preceding bytes, then reach the
  backend unchanged. Real list iterators preserve list mutation and finalizer
  order. Generators and container subclasses keep the eager Python loop, so
  they can inspect or update the stream between yields.
- BLAKE3 still implements every cryptographic operation. No new algorithm,
  retained hasher, key/context cache, global pool, or inherited lock is involved.
  Compiler-free installs retain the Python stream path.

Public signatures, exports, constants, digest values, truncation, validation,
constant-time tag comparison, and reusable/fork-safe streaming are unchanged.

## Paired measurements

The run contains 58 workloads and 25 alternating batches per workload. Inputs
are prepared outside timing; both sides must produce equal values and types
before timing. The harness also checks public signatures, exports, constants,
and source hashes. It retains every sample, paired median ratios, minima,
versions, and noise flags. Saved modules and private helpers load independently.

The host ran CPython 3.13.14, BLAKE3 1.0.10, and macOS arm64 with 18 logical CPUs.
The ending one-minute load average was **47.38**. **54 of 58 workloads were
noisy** by the 25% spread criterion. Stream batching shows a clear local gain;
parallel worker latency depends strongly on contention. These are measurements
of this host, not portable performance floors.

Times below are the median batch costs in microseconds. The last column is the
median of each round's before/after ratio; it is computed independently of the
two median costs. The complete JSON also preserves minimum-cost estimators.

| Operation / input | Before µs | After µs | Paired median ratio |
| --- | ---: | ---: | ---: |
| `Stream / 64 KiB / 1 B chunks` | 4333.500 | 302.216 | 14.50× |
| `Stream / 1024 KiB / 64 B chunks` | 2296.271 | 695.196 | 3.29× |
| `Stream / 1 MiB / 1 KiB chunks` | 1478.417 | 606.505 | 2.33× |
| `Stream / 1024 KiB / 4096 B chunks` | 625.911 | 620.238 | 1.00× |
| `Stream iterator / 1 MiB / 1 KiB chunks` | 1344.844 | 1346.073 | 0.99× |
| `raw / 1 MiB` | 617.005 | 1229.489 | 0.46× |
| `raw / 2 MiB` | 1205.104 | 2810.076 | 0.65× |
| `hex / 2 MiB` | 1071.847 | 762.438 | 1.57× |
| `mac_raw / 1 MiB` | 578.648 | 253.196 | 2.24× |
| `mac_raw / 2 MiB` | 1084.608 | 440.518 | 2.46× |
| `mac_hex / 2 MiB` | 1085.823 | 471.200 | 2.24× |
| `Stream / 64 B / 64 B chunks` | 0.507 | 0.552 | 0.94× |
| `Stream / empty` | 0.453 | 0.474 | 0.96× |

The native path improves one-byte chunks by **14.50×**, 64-byte chunks by
**3.29×**, and 1 KiB chunks by **2.33×** by paired median. Already-large chunks
and the unchanged iterator path remain around 1×. The added dispatch check
costs roughly 20–45 ns for empty/single-chunk streams on this host.

Fresh pools can lose under load. The 1 MiB raw hash measures 0.46× and the
2 MiB raw hash 0.65× by paired median, although their fastest batches improve
1.43× and 1.86×. MACs over the same sizes measure 2.24× and 2.46× by paired
median. The implementation makes no latency guarantee under oversubscription.
A preliminary 512 KiB cutoff was rejected because thread startup outweighed
its gains; fewer than eight stream chunks also retain the Python path.

Reproduce against a complete source snapshot containing `unstd/crypto` and its
private helpers. Build the optional extension first to measure native batching:

```console
.venv/bin/python bench/crypto_hash.py --compare-source /path/to/before --rounds 25 --seconds 0.005 --output /tmp/crypto-hash-adaptive.json
```

## Validation

- CPython 3.13.14: 830 crypto, MAC primitive, and hashing benchmark checks pass;
  113 packaging/optional-collection/benchmark gates also pass.
- CPython 3.12.13 and 3.14.6 with BLAKE3 1.0.10: 818 crypto and MAC primitive
  checks pass on each interpreter with its native extension loaded.
- CPython 3.13.14: 482 stream checks pass with AddressSanitizer and
  UndefinedBehaviorSanitizer, including native and forced Python paths.
- 148 compiled-wheel packaging, pristine-consumer, and CI workflow checks pass,
  including offline wheel/sdist rebuilds, optional compiler failures, base
  imports, and real installed crypto dispatch. Release closure gates pass.
- Ruff checks/formatting and strict mypy pass for the crypto modules and
  comparison harness.

Coverage includes published known answers, adaptive dispatch boundaries, CPU
caps, mutable and multidimensional buffers, custom widths and errors, thread
refusal, concurrent independence, real fork subprocesses, reentrant generators,
live list mutation, finalizer ordering, bounded copies, exception cleanup, and
interruptible long runs of empty chunks.
