# Containers: native read queries and lazy backend imports

Historical read-query evaluation, measured 2026-10-25. The later
[mutation evaluation](mutations/README.md) records the current mutation kernels
and their additional safety checks.

Measured 2026-10-25. The package keeps `sortedcontainers`' segmented-list layout
and adds C rank/position kernels to compatible subclasses. Collection mutations,
block maintenance and index building execute upstream code. `Map` remains the
original native `immutables.Map`. Each family now loads only when requested.

The measured hosts are **ARM64 macOS (Python 3.14.9)** and an **ARM64 Debian Linux
container on Docker's Linux VM (Python 3.14.8)**. Both use sortedcontainers 2.4.0
and immutables 0.21. Ratios compare two revisions on the same host/interpreter;
these receipts do not establish x86_64 or bare-metal Linux timings.

## Selected query results

At 65,536 entries, seven alternating rounds, 512 probes and eight prepared
collections per sample, median before/current wall-time ratios were:

| Query | Linux | macOS |
| --- | ---: | ---: |
| SortedList rank (`bisect_left`) | 1.65× | 1.58× |
| SortedList indexed reads | 1.72× | 1.70× |
| Keyed-list rank | 1.17× | 1.29× |
| Keyed-list indexed reads | 1.40× | 1.44× |

A ratio above 1 means the current implementation was faster. These gains target
rank and positional queries. Other methods are not uniformly faster: the
4,096-entry `index(value)` case measured 0.88×
on Linux and 0.95× on macOS. All operation
results, including losses, appear below. Unchanged Map operations also vary
between processes; small differences should not be treated as universal wins
or regressions.

## Fresh-process startup

Eleven alternating rounds, warmed bytecode, normal cyclic GC. Timings include
first construction for member imports, so backend loading is included.

| Scenario | Linux ms before → current | Ratio | macOS ms before → current | Ratio |
| --- | ---: | ---: | ---: | ---: |
| Package import | 18.87 → 4.16 | 4.54× | 13.75 → 2.66 | 5.18× |
| Map import + first construction | 24.98 → 6.83 | 3.66× | 13.94 → 5.34 | 2.61× |
| SortedList import + first construction | 21.57 → 20.69 | 1.04× | 15.09 → 12.70 | 1.19× |
| All four types + first constructions | 23.48 → 21.30 | 1.10× | 13.01 → 13.97 | 0.93× |

Package import loads neither backend. Map loads only immutables; sorted types
load only the sorted backend. Importing every type still loads both families.
Resolved names are cached; public discovery, submodule attributes, wildcard
imports and missing-extra errors retain their behavior.

## Method and provenance

The [paired driver](../../profiles/containers.py) isolates before/current imports
in fresh subprocesses, alternates source order and checks every prepared result
against plain list/dict models outside timing. It rejects source/dependency
drift, the wrong extension path and missing native dispatch. Reports retain
wall/process CPU samples, source/binary hashes, selected descriptor owners,
interpreter/compiler configuration and dependency fingerprints.

The 4,096-entry run uses 11 rounds, batches of 256 and 32 independent states;
the 65,536-entry run uses seven rounds, batches of 512 and eight states. Cases
include random/keyed inserts, index creation, removals, rank, indexing, slices,
ranges, construction, batch updates and immutable-map edits. Construction is
normalized per input item; range/slice operations use their recorded units.
Cyclic GC is disabled only inside operation timing and restored afterward;
allocation and reference-count cleanup remain measured. Cold imports retain
normal GC. Both sources prewarm one private bytecode cache before fresh-process
timing. No machine-independent timing threshold or existing floor was lowered.

The original source is preserved in [before-source.tar.gz](before-source.tar.gz).
A current source snapshot and exact hashes are in
[after-source.tar.gz](after-source.tar.gz) and
[source-manifest.json](source-manifest.json).

## Memory and compatibility qualification

The final [C implementation](../../../native/containers/src/adapter.c) performs
no native collection-array mutations. It retains owned references across
callbacks, checks list accesses and integer arithmetic, refreshes instance state
after comparisons/index builders, and delegates arbitrary user subclasses or
unsupported argument/layout types to upstream methods. Comparison dispatch uses
stdlib `_bisect`. The fast path is enabled only for verified sortedcontainers
2.4.0; missing kernels, other interpreters and unqualified backend versions use
the original classes. Accelerated sorted classes have new identities and remain
upstream subclasses. Constructor signatures, keys, views, copies and pickle
round trips are tested; upstream classes are never patched.

- [Linux ASan/UBSan main checks](linux-asan-main.txt): 46 passed, four expected
  missing-backend skips.
- [ASan/UBSan owned workers](linux-asan-children.txt): 33 passed. These include
  reentrant comparisons, dictionary replacement/finalizers, active-call
  reinstallation and corrupted state/configuration.
- Lifetime checks reclaim 96 ephemeral types and instances after 576 kernel
  installations. Mixed histories perform 24,000 operations across keyed and
  keyless collections at block loads 4, 8 and 16, checking upstream/list models
  and internal indexes. Fourteen malformed-state/configuration probes run in
  bounded owned interpreters.
- Debug-allocator containers checks: 79 passed/four expected skips on
  [Python 3.12.15](macos-py312-tests.txt),
  [3.13.17](macos-py313-tests.txt) and
  [Linux 3.14.8](linux-tests.txt). macOS 3.14.9 additionally runs 33 benchmark
  harness checks: [112 passed/four expected skips](macos-tests.txt).
- [Registry, public API, workflow and release gates](gates-tests.txt): 282 passed.
- [Installed wheel checks](wheel-tests.txt): 79 passed/four expected skips.
  [Wheel/sdist receipt](wheel-receipt.json) verifies the packaged extension,
  native source, typing stub and attribution.

The sanitizer build uses GCC 14.2 with
`-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer`, fatal ASan/UBSan
errors and `PYTHONMALLOC=malloc`. LeakSanitizer is disabled for the
uninstrumented interpreter/dependency runtime; weak references test native
configuration/class lifetimes directly. The
[qualification driver](../../../native/containers/qualify_memory.py) partitions
main tests and owned-worker tests so both execute instrumented code while
retaining the real worker deadlines, working directories and group cleanup.
This handles a [reproduced GCC ASan/ARM64 fork-runtime failure](linux-sanitizer-fork.json)
that occurs before exec even when the child imports no product code. The worker
launcher itself stays uninstrumented; its children load the instrumented kernel.
No sanitizer failure is suppressed.

## Complete wall-time ratios

| Workload | Linux 4k | macOS 4k | Linux 64k | macOS 64k |
| --- | ---: | ---: | ---: | ---: |
| `sorted_list.construct` | 1.00× | 0.98× | 0.98× | 0.96× |
| `sorted_list.add` | 0.99× | 1.01× | 1.14× | 0.93× |
| `sorted_list.add_random` | 0.87× | 0.97× | 1.17× | 0.92× |
| `sorted_list.add_indexed` | 0.87× | 0.97× | 0.91× | 0.88× |
| `sorted_list.remove` | 1.06× | 0.99× | 1.05× | 1.03× |
| `sorted_list.bisect` | 1.18× | 1.19× | 1.65× | 1.58× |
| `sorted_list.getitem` | 1.24× | 1.27× | 1.72× | 1.70× |
| `sorted_list.index` | 0.88× | 0.95× | 0.97× | 0.95× |
| `sorted_list.slices` | 1.18× | 1.27× | 1.64× | 1.72× |
| `sorted_list.irange` | 0.99× | 1.01× | 1.01× | 1.12× |
| `sorted_list.update` | 0.94× | 1.03× | 1.00× | 0.91× |
| `sorted_list.keyed.construct` | 0.95× | 1.00× | 0.97× | 0.85× |
| `sorted_list.keyed.add` | 1.04× | 1.01× | 1.06× | 0.89× |
| `sorted_list.keyed.add_random` | 1.07× | 0.96× | 1.00× | 0.84× |
| `sorted_list.keyed.add_indexed` | 0.92× | 0.96× | 0.94× | 0.87× |
| `sorted_list.keyed.remove` | 0.95× | 1.01× | 1.01× | 0.99× |
| `sorted_list.keyed.bisect` | 1.11× | 1.07× | 1.17× | 1.29× |
| `sorted_list.keyed.getitem` | 1.27× | 1.23× | 1.40× | 1.44× |
| `sorted_list.keyed.index` | 1.00× | 1.07× | 1.29× | 1.27× |
| `sorted_list.keyed.slices` | 1.12× | 1.28× | 1.49× | 1.65× |
| `sorted_list.keyed.irange` | 1.09× | 0.98× | 0.99× | 0.98× |
| `sorted_list.keyed.update` | 0.99× | 1.01× | 0.97× | 0.86× |
| `sorted_dict.construct` | 1.34× | 1.13× | 1.12× | 0.99× |
| `sorted_dict.insert` | 0.94× | 1.01× | 0.94× | 0.99× |
| `sorted_dict.insert_random` | 1.02× | 1.02× | 0.85× | 0.88× |
| `sorted_dict.update` | 1.01× | 1.05× | 0.84× | 1.06× |
| `sorted_set.construct` | 1.16× | 1.01× | 0.92× | 1.04× |
| `sorted_set.add` | 1.12× | 1.02× | 0.97× | 0.98× |
| `sorted_set.add_random` | 1.02× | 1.00× | 0.88× | 1.04× |
| `sorted_set.update` | 0.97× | 1.05× | 0.95× | 0.82× |
| `map.construct` | 1.07× | 1.03× | 1.08× | 0.85× |
| `map.set` | 0.97× | 0.98× | 1.02× | 0.98× |
| `map.update` | 0.97× | 0.97× | 1.00× | 0.82× |
| `map.mutate` | 0.98× | 0.99× | 0.94× | 0.96× |

Raw samples: [Linux 4k](linux-py314.json), [Linux 64k](linux-large-py314.json),
[macOS 4k](macos-py314.json), [macOS 64k](macos-large-py314.json).

## Reproduce

Use an interpreter with the containers extra and the compiled current extension.
Run the driver directly so no benchmark package imports prewarm the product:

```console
mkdir -p /tmp/containers-before
tar -xzf bench/reports/containers/before-source.tar.gz -C /tmp/containers-before
python bench/profiles/containers.py --compare-source /tmp/containers-before/src --source src --rounds 11 --require-native --output /tmp/containers-small.json
python bench/profiles/containers.py --compare-source /tmp/containers-before/src --source src --size 65536 --batch 512 --iterations 8 --rounds 7 --require-native --output /tmp/containers-large.json
```

The native [source guide](../../../native/containers/README.md) describes the
sanitizer build and two qualification phases. Re-run on the deployment host for
its workload and CPU architecture.
