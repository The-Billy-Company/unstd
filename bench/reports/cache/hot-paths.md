# Typed cache in a native preprocessing workflow

The final owner-run workflow measured **1.688× warm reuse on macOS arm64** and
**1.560× on Linux arm64**. macOS cold processing improved 1.189×; Linux cold
processing was effectively neutral at 1.018×. These results describe one
synthetic CPU workload, not GPU training throughput or portable latency.

The [sanitized numeric receipt](hot-paths.json) retains all nine wall samples per
mode, intermediate losses, source identities, validation counts, and platform
limits. The application, exact source snapshots, fixture bodies, and complete
owner receipts remain private. Public cache profiles exercise separate package
contracts; they cannot independently reproduce this private caller. The owner
retains its source-backed driver and exact comparison snapshots.

## Complete workflow

Each timed invocation freshly validates source provenance and ingests 1,024
synthetic rows, encodes two markers, constructs preprocessing and its complete
recipe/cache identity, and executes native dataset construction, map, filter,
response masking, and sequence packing. There are 487,166 token IDs and 163,840
trainable labels at a 512-token row limit. Common native tokenizer fixture setup
and proof-seam compilation are outside both timers. Work counters run separately
outside timing, without patching the measured workflow.

| Platform | Original scalar | Batched uncached | Typed cold | Typed warm |
| --- | ---: | ---: | ---: | ---: |
| macOS arm64 | 652.362 ms | 474.562 ms | 548.615 ms | 386.580 ms |
| Linux arm64 VM | 645.026 ms | 514.763 ms | 633.385 ms | 413.563 ms |

The estimator is the median of nine interleaved paired rounds. Ratios compare
medians within the same capture; raw times from different hosts or captures are
not interchangeable. Desktop-host variation remains in the raw samples. The
1.8% Linux cold difference is too small to claim a reliable improvement.

Every result checks complete IDs, response labels, and packed positions against
the actual original scalar implementation. Cold processing renders all rows and
uses two native 512-row encodes. Warm processing performs **zero row renders and
zero row encodes**, retaining the two common marker encodes. The timing corpus
retains all rows and does not merge multiple examples into one packed row;
dedicated tests cover other truncation, masking, and packing shapes.

The caller uses validated binary token artifacts, complete content/recipe keys,
bounded native batches, and explicit native dataset fingerprints derived from
the input dataset and admitted transformation identity. Input construction,
provenance admission, and output validation remain in the path. Persistent store
keys retain full-content SHA256 identities. Older installations without the
typed codec capability continue through an uncached compatible path.

## Losses retained

An intermediate complete-workflow capture still serialized live SDK/cache object
graphs for native dataset fingerprints. Its cold result was **10.6% slower on
macOS** (580.263 → 641.844 ms) and **12.6% slower on Linux**
(757.532 → 852.724 ms). That finding prompted the actual fingerprint API change;
the raw paired captures remain in the JSON history.

The final narrow 128-row callback diagnostic also retains a Linux cold loss:
21.499 → 33.221 ms. It excludes construction, ingestion, dataset conversion,
filtering, and packing, and is distinct from the full 512-row production shape.
FULL-synchronized writes and binary admission have costs; small or single-use
producers need not benefit from persistence.

A separate caller-owned bounded in-memory schema-admission improvement measured
10.48–29.28× repeated-call gains on macOS and 9.44–28.76× on Linux. It still
validates every argument and never caches argument verdicts. Unique cold schemas
were 1.47% slower on macOS and 2.77% slower on Linux. These are **not** persistent
`Cache` or codec speedups; their separate timings are retained for attribution.

## Platform and package evidence

Both workflows use the real native fast-tokenizer SDK without downloaded models:
transformers 5.17.0, tokenizers 0.23.3, datasets 5.0.1, PyArrow 24.0.0, and
Polars 2.0.0. The native thread environment is unset, matching the caller default.
macOS uses CPython 3.14.9; Linux uses CPython 3.14.8.

Linux runs native aarch64 ELF binaries and a Linux kernel inside a Docker Desktop
VM on a macOS arm64 host, with container-owned overlayfs and a four-CPU/four-GiB
limit. This is not bare-metal, x86_64, or native Windows qualification.

- **144 cache tests** pass on macOS and in each installed Linux native and
  no-extras/no-native environment. A pristine only-unstd wheel probe also passes.
- **77 native caller tests** pass on each platform with zero failures or skips.
  They cover exact scalar parity, warm reuse across processes, configuration and
  source invalidation, corrupt frames, publication/producer failures, cancellation,
  provenance admission, and packing.
- The current scoped package check passes **182 tests**: 144 cache and 38
  benchmark-coverage tests. Strict mypy passes six cache/driver files plus two
  Windows-targeted files; scoped caller Pyright and Ruff/format checks also pass.
  These scoped results do not certify either entire repository as clean.
- An 88-byte Linux-produced numeric frame decodes exactly on macOS and
  re-encodes byte-identically. Its SHA256 is
  `d98449295031f2c9a2f14356ce4846da9edb633186b93db11135fe0e290e882f`.
  This is a CPU frame check, not arbitrary database/model portability.

The installed Linux wheel SHA256 is
`67d002fc907a4b9e991a9b28fef0c6daddf4daddfc1b2b3431833a0d362178f9`.
The measured cache source is
`3ff4aecc4cc8fd68807de62fcec1125cb58e20cb4e7b0a0e92241dfcad8655bd`;
locking is `eab1a7949bcde294e81834e34fa4947b6190099652b7aaf6092b2044c6ade19c`.
Opaque hashes for the private caller and proof are in the numeric receipt.

## Controlled Linux syscall counts

Untimed import/seed warmup precedes independent zero-versus-100 replacement
processes for each installed wheel. Subtraction removes each version's startup,
constructor, and seed work. For 100 committed JSON replacements:

| Selected calls | Before | After |
| --- | ---: | ---: |
| `newfstatat` | 2,700 | 1,100 |
| `openat` | 500 | 500 |
| `fdatasync` | 500 | 500 |
| `flock` | 200 | 200 |
| Total selected traced calls | 4,200 | 2,600 |

Preparing canonical lock identity once removes 16 `newfstatat` calls per
replacement in this probe. The result preserves the measured opens, lock calls,
and synchronization calls; it is not an elapsed-time speedup claim. These are
replacement counts, not warm-read counts. The JSON retains both source and wheel
identities for this comparison.

See the [validation record](validation.md), [operation contracts](codec-operations.md),
and [package limits](../../../src/unstd/cache/README.md) for the corresponding
scope. Entry counts do not bound physical database size, and token-frame budgets
do not bound all Python/SDK memory. No model-quality, GPU, network, or end-to-end
training-duration claim follows from these measurements.
