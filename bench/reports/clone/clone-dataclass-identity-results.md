# Dataclass identity-preserving clone measurements

`clone.deep` now uses a native helper for conservatively admitted dataclasses.
The helper combines allocation, early registration of the twin in the shared
memo, state copying and dictionary update. It avoids repeated Python calls at
those boundaries. A bounded weak-reference cache retains class admission
metadata without retaining dynamically generated classes; class version changes
invalidate the cached admission. Unsupported layouts keep the Python path.

Shared-reference identity remains part of the contract. Repeated fields,
references from outer containers, dataclass cycles and custom `__deepcopy__`
implementations continue to use one memo. The change does not batch records or
restart already allocated twins when user callbacks change the source.

The baseline is the exact wrapper, native C source and CPython 3.13 extension
saved immediately before this dataclass optimization. It already includes the
pointer-keyed builtin identity map from the
[previous optimization](clone-shared-identity-optimized-results.md). Both sides
preserve aliases; `copy.deepcopy` is an additional control in the raw data.

## Process CPU results, GC disabled

| Input | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| 200 distinct dataclasses (original case) | 228.28 µs | 200.38 µs | 1.14× |
| 500 scalar-only dataclasses | 527.00 µs | 475.36 µs | 1.11× |
| 500 homogeneous rows with list fields | 683.25 µs | 623.39 µs | 1.10× |
| 200 rows sharing fields and an outer alias | 228.76 µs | 204.44 µs | 1.12× |
| One dataclass referenced 500 times | 54.86 µs | 56.16 µs | 0.98× |
| 100 nested mixed dataclass branches | 240.77 µs | 224.28 µs | 1.07× |
| 200 frozen dataclasses | 278.72 µs | 236.85 µs | 1.18× |
| 100 rows beside a custom copy hook | 107.81 µs | 103.24 µs | 1.04× |
| 200 distinct nested builtin records (control) | 49.57 µs | 50.34 µs | 0.98× |
| Shared 1,000-item builtin list × 500 (control) | 7.58 µs | 7.85 µs | 0.97× |

## Process CPU results, GC enabled

| Input | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| 200 distinct dataclasses (original case) | 254.38 µs | 227.43 µs | 1.12× |
| 500 scalar-only dataclasses | 534.43 µs | 449.00 µs | 1.19× |
| 500 homogeneous rows with list fields | 630.14 µs | 578.44 µs | 1.09× |
| 200 rows sharing fields and an outer alias | 221.64 µs | 191.00 µs | 1.16× |
| One dataclass referenced 500 times | 57.89 µs | 55.97 µs | 1.03× |
| 100 nested mixed dataclass branches | 247.53 µs | 236.29 µs | 1.05× |
| 200 frozen dataclasses | 229.89 µs | 200.60 µs | 1.15× |
| 100 rows beside a custom copy hook | 110.68 µs | 96.08 µs | 1.15× |
| 200 distinct nested builtin records (control) | 57.92 µs | 57.61 µs | 1.01× |
| Shared 1,000-item builtin list × 500 (control) | 8.62 µs | 8.31 µs | 1.04× |

The original 200-dataclass case improves by **1.14× with GC disabled and
1.12× with GC enabled**. This more than offsets the small 2–3% regression seen
in the previous identity-map measurements. Scalar-only rows improve by
1.11–1.19×, list-bearing homogeneous rows by 1.09–1.10×, frozen rows by
1.15–1.18×, and rows beside a custom copy hook by 1.04–1.15×. Shared-field rows
improve by 1.12–1.16×, with their identities checked before timing.

Nested mixed classes improve by 1.05–1.07×. A repeatedly referenced single
record has small mixed changes across GC modes; this is not a universal speedup
claim. The two builtin controls stay within roughly 4% of the saved
implementation in these samples, retaining the earlier builtin-tree
improvements. Small differences of that size should be rechecked on a quiet
performance worker.

## Method and reproducibility

Each comparison uses nine alternating-order rounds with prepared inputs,
process CPU and elapsed clocks, adaptive batch sizes and output destruction
included. The GC-enabled run uses ordinary interpreter thresholds; the other
run disables cyclic GC during timed batches. Input construction, explicit
collection and correctness checks are outside timing.

An independent graph-aware checker validates exact types, dictionary order,
source isolation, reuse of clones for repeated references and separation of
equal-but-distinct source nodes. It covers aliases across dataclass fields,
outer containers and a custom copy hook. Classes and admission caches are warm
from these untimed checks; first-ever admission of a new dynamic class is not
measured here.

These are local CPython 3.13 measurements on the development machine, not
performance floors. Background activity, allocator layout and interpreter
versions affect timings. The paired comparison is the relevant evidence;
absolute times should not be compared across separate report runs.

[Raw results](clone-dataclass-identity-py313.json) include every CPU/wall sample,
source and extension fingerprints, class-layout diagnostics, the complete
measurement harness and saved pre-change Python/C sources under
`before_sources`. Rebuild those saved sources with the same interpreter and
build settings, then run the embedded harness with `--before-dir`, `--output`
and `--rounds 9` to reproduce the comparison. The saved manifest records the
original extension hash.
