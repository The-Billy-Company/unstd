# Containers: safe batch mutation acceleration

Existing `SortedList` APIs now accelerate substantial integer batches. The chosen workload is an ordered ID/timestamp index with increasing ingestion and prefix expiration. This follows the package’s documented index/range use cases; it is a selected workload, not usage telemetry. `Map` retains its existing native implementation.

These results compare the preceding read-accelerated backend with the new kernel-version-3 backend. They repeat the same `update(...)` or `del values[start:stop]` call on both sources. Comparisons against per-item loops appear separately in the JSON and do not count as native speedups.

The hosts are ARM64 macOS with CPython 3.14.9 and ARM64 Debian Linux with CPython 3.14.8 in Docker’s Linux VM. Both use sortedcontainers 2.4.0 and immutables 0.21. These measurements establish neither x86_64 nor bare-metal/server timings.

## One-million-entry index

Each batch contains approximately 100,000 entries. Seven alternating rounds use fresh workers and independently checked results. Times below are median complete API-call latencies in milliseconds; ratio is before/current.

| Workload | Linux ms before → current | Ratio | macOS ms before → current | Ratio |
| --- | ---: | ---: | ---: | ---: |
| Prefix, aligned, retained IDs | 62.798 → 0.197 | 318.91× | 58.053 → 0.206 | 281.30× |
| Prefix, 250-entry partial boundary | 60.810 → 0.286 | 212.69× | 57.072 → 0.286 | 199.73× |
| Prefix, 750-entry partial boundary | 64.653 → 0.399 | 161.90× | 60.993 → 0.409 | 149.28× |
| Suffix, aligned, retained IDs | 63.692 → 0.192 | 332.38× | 57.513 → 0.202 | 285.01× |
| Suffix, 750-entry partial boundary | 62.256 → 0.757 | 82.24× | 57.130 → 0.719 | 79.43× |
| Prefix, exclusively owned IDs | 59.755 → 0.624 | 95.77× | 57.016 → 0.509 | 112.06× |
| Increasing tuple update | 15.438 → 1.061 | 14.55× | 14.895 → 1.161 | 12.82× |
| Increasing list update | 15.307 → 1.099 | 13.92× | 14.645 → 1.079 | 13.58× |

**The 100× target applies to the measured large expiration batches.** Increasing inserts improve by a smaller factor. Scalar edits, small batches, keyed/generic values, ownership costs and partial boundaries have different results. The complete tables below include regressions.

For example, clearing a 128-entry list measured about 0.46 → 0.80 µs on Linux
and 0.39 → 0.59 µs on macOS. The macOS rank-query control measured 0.78×.
These costs remain visible alongside the batch gains.

Ordinary fixtures retain ID objects outside the index, as a record store may do. The `exclusive_ids` controls construct fresh integers above the small-integer cache, retain only surviving objects externally, and include freeing removed integers during timing. Partial boundaries delegate at most one block to upstream; underflow can also trigger a merge.

## Behavior and safety

The optional native path keeps the upstream segmented layout and compatible subclasses. It is enabled only for sortedcontainers 2.4.0. Other versions or missing native builds retain upstream classes. Arbitrary user subclasses, keyed values, unsupported arguments and overridden helpers delegate upstream. The upstream classes themselves are not patched.

- Deletion skips the Python per-element position/delete loop for whole prefix/suffix blocks below the upstream rebuild threshold. Removed values must be exact integers.
- Updates stage a replacement tail for nondecreasing exact signed-64-bit integer tuples/lists at or above the old maximum. Small, overlapping or unsupported batches use upstream.
- Native staging owns its references, checks arithmetic and geometry, rejects active array/iterator aliases, and revalidates class/configuration, helpers, fields and mutable payload identities before commit.
- Four coupled metadata replacements pin the old state and roll it back on injected failure. Generic callbacks enter upstream after temporary snapshots are released.
- Layout admission uses valid CPython type version tags on 3.12–3.14; class/base changes invalidate it. Python 3.12 also requires its validity flag. Other minor versions use uncached checks. The cache adds no class references.

Successful batches invalidate the positional index. A subsequent rank/index query that needs it rebuilds it upstream. The mutation timings exclude that later query. Individual add/remove/discard/pop algorithms remain upstream; scalar deletion retains dispatch overhead. Small or unsupported operations have no universal speedup.

## Qualification

- macOS production/debug allocator: [303 passed, 41 expected skips](macos-tests.txt).
- macOS testing/debug allocator: [340 passed, four expected skips](macos-hook-tests.txt).
- Python 3.12 and 3.13 debug allocator: 303 passed/41 expected skips each ([3.12 receipt](macos-py312-receipt.json), [3.13 receipt](macos-py313-receipt.json)).
- Linux production/debug allocator: [303 passed, 41 expected skips](linux-tests.txt).
- Linux ASan/UBSan: [78 main-process tests](linux-asan-main.txt), plus [262 tests using 264 instrumented owned workers](linux-asan-children.txt); four backend-absent-only cases skip.
- Exhaustive fault sweeps cover 10 prefix, 10 suffix and 1,070 append staging/commit injection points, checking original state and object identities after each failure ([counts](failpoint-counts.json)). Staging callbacks exercise GC, aliases, dictionary replacement, helper/type/configuration changes and mutable input edits.
- Mixed small-load histories, comparison/finalizer order, split/merge behavior, views, pickles, and reclamation of 96 temporary classes/instances after 576 installations remain checked. Namespace and descriptor tests include 2,048 class/base mutations to exercise cache invalidation.
- [385 API, workflow, release and benchmark gates](gates-tests.txt); [strict typing](typing-tests.txt).
- An actual Hatch sdist was used to build the wheel, then clean-installed: [303 passed, 41 expected skips](artifact-wheel-tests.txt). The [artifact receipt](artifact-wheel-receipt.json) verifies source/stub/binary inventories, kernel version 3, absent production test hooks, and both real mutation fast paths.

Production skips comprise 37 instrumentation-only cases and four backend-absent-only cases. The required-hook environment makes missing hooks fail instrumented qualification. Tests preserve return values, survivor identities, duplicate order, user exception identities, supported callback/finalizer order and upstream partial failure states.

The Linux sanitizer build uses GCC 14.2, `-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -DUNSTD_CONTAINERS_TESTING=1`, fatal ASan/UBSan errors and `PYTHONMALLOC=malloc`. LeakSanitizer is disabled for the uninstrumented interpreter/dependencies; explicit weak references test native class/instance lifetimes. The [driver](../../../../native/containers/qualify_memory.py) runs main tests and owned children in separate partitions to avoid the previously reproduced GCC ASan/ARM64 failure before exec. Real worker deadlines, working directories and cleanup remain active.

## Query controls

At 65,536 entries, seven paired rounds also check the existing read paths after adding safety checks. Ratio above 1 means faster.

| Query | Linux | macOS |
| --- | ---: | ---: |
| `sorted_list.bisect` | 1.01× | 0.78× |
| `sorted_list.getitem` | 1.06× | 0.92× |
| `sorted_list.index` | 1.04× | 0.97× |
| `sorted_list.keyed.bisect` | 1.01× | 0.93× |
| `sorted_list.keyed.getitem` | 0.95× | 0.95× |

[Linux query/control samples](linux-query-controls.json) and [macOS query/control samples](macos-query-controls.json) retain every case, including unchanged Map controls. Small differences and isolated losses should not be treated as universal performance guarantees.

## Method and reproduction

The [mutation driver](../../../profiles/containers_mutations.py) records alternating fresh processes, dependency/compiler fingerprints, source and binary hashes, wall and CPU samples, and native descriptor selection. Separate profiling passes reset mutation counters after setup and prove actual commits before timing. Model, return and identity checks run outside timing. Cyclic GC is disabled only around operation clocks; reference-count cleanup remains measured. A shared bytecode cache is warmed before measured workers. There is no timing threshold lowered to admit this change.

Collection sizes are 128, 4,096, 65,536 and 1,000,000. The first three run the complete mutation matrix; the million-entry run selects slice, append and isolated SortedList mutations. Iterations are capped at `min(8, max(1, 262144 // size))`; all runs use seven rounds. Scalar batches use up to 2,048 edits; slice/append cases use approximately n/10. Fixtures use the supported default block load of 1,000.

The [qualification manifest](qualification.json) records compiler settings, binary hashes and check results. The [before source](before-source.tar.gz), [after qualification source](after-source.tar.gz) and [source manifest](source-manifest.json) freeze the relevant runtime, harness and test inputs. These are qualification snapshots; the artifact receipt describes the separately built complete sdist/wheel. Build the single extension in each extracted root with an interpreter containing setuptools and the recorded dependencies:

```python
from pathlib import Path
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext

for label in ("before", "after"):
    root = Path(f"/tmp/containers-{label}")
    extension = Extension(
        "unstd.containers._sorted_native",
        [str(root / "native/containers/src/adapter.c")],
    )
    command = build_ext(Distribution({"ext_modules": [extension]}))
    command.build_lib = str(root / "src")
    command.build_temp = str(root / "build")
    command.ensure_finalized()
    command.run()
```

```console
python bench/profiles/containers_mutations.py --compare-source /tmp/containers-before/src --source /tmp/containers-after/src --size 65536 --rounds 7 --require-native-mutations --output /tmp/containers-64k.json
python bench/profiles/containers_mutations.py --compare-source /tmp/containers-before/src --source /tmp/containers-after/src --size 1000000 --rounds 7 --include slice. --include append. --include isolated. --require-native-mutations --output /tmp/containers-million.json
```

Repeat at 128 and 4,096 entries, omitting the native-mutation requirement for 128 where no batch qualifies. Run the product suite with `PYTHONPATH=src PYTHONMALLOC=debug python -m pytest tests/suites/product/containers --require-extra containers -q` from the extracted after root. Sanitizer reproduction additionally compiles the testing macro, requires hooks and uses both driver partitions described in the [native guide](../../../../native/containers/README.md).

## Complete mutation wall-time ratios

All cases measured at 65,536 entries are shown below; the million-entry column is limited to the selected SortedList cases. Timings normalize by the case’s recorded element operations. A dash means that case was not selected at that size.

| Case | Linux 64k | macOS 64k | Linux 1m | macOS 1m |
| --- | ---: | ---: | ---: | ---: |
| `steady.sorted_list.random_replace.unindexed` | 0.87× | 1.02× | — | — |
| `steady.sorted_list.rolling_expiration.unindexed` | 0.94× | 0.99× | — | — |
| `steady.sorted_list.random_replace.indexed` | 1.02× | 0.93× | — | — |
| `steady.sorted_list.rolling_expiration.indexed` | 1.07× | 1.00× | — | — |
| `steady.sorted_set.random_replace.unindexed` | 1.09× | 1.03× | — | — |
| `steady.sorted_set.rolling_expiration.unindexed` | 1.02× | 0.98× | — | — |
| `steady.sorted_set.random_replace.indexed` | 1.04× | 0.92× | — | — |
| `steady.sorted_set.rolling_expiration.indexed` | 0.96× | 0.91× | — | — |
| `steady.sorted_dict.random_replace.unindexed` | 1.02× | 0.97× | — | — |
| `steady.sorted_dict.random_replace.indexed` | 1.01× | 0.98× | — | — |
| `fallback.custom_comparisons.random_replace` | 0.92× | 1.03× | — | — |
| `fallback.keyed_records.random_replace` | 0.97× | 0.97× | — | — |
| `isolated.sorted_list.add.unindexed` | 0.85× | 1.03× | 0.99× | 0.86× |
| `isolated.sorted_list.remove.unindexed` | 0.90× | 1.01× | 0.90× | 0.90× |
| `isolated.sorted_list.discard.unindexed` | 0.99× | 0.93× | 1.11× | 1.00× |
| `isolated.sorted_list.pop.unindexed` | 0.98× | 0.96× | 1.02× | 1.01× |
| `isolated.sorted_list.delitem.unindexed` | 0.87× | 0.87× | 0.96× | 0.92× |
| `slice.sorted_list.prefix.fraction.unindexed` | 15.35× | 17.07× | 361.75× | 307.54× |
| `slice.sorted_list.suffix.fraction.unindexed` | 75.04× | 73.65× | 316.44× | 302.59× |
| `slice.sorted_list.prefix.aligned.unindexed` | 120.71× | 109.38× | 318.91× | 281.30× |
| `slice.sorted_list.prefix.partial_kept_large.unindexed` | 25.84× | 25.05× | 212.69× | 199.73× |
| `slice.sorted_list.prefix.partial_underflow.unindexed` | 13.23× | 13.02× | 161.90× | 149.28× |
| `slice.sorted_list.suffix.aligned.unindexed` | 143.80× | 141.04× | 332.38× | 285.01× |
| `slice.sorted_list.suffix.partial_kept_large.unindexed` | 20.64× | 19.31× | 150.57× | 119.80× |
| `slice.sorted_list.suffix.partial_underflow.unindexed` | 8.57× | 7.87× | 82.24× | 79.43× |
| `isolated.sorted_list.add.indexed` | 0.98× | 1.09× | 1.06× | 1.05× |
| `isolated.sorted_list.remove.indexed` | 0.89× | 1.04× | 1.01× | 0.96× |
| `isolated.sorted_list.discard.indexed` | 0.94× | 0.98× | 1.04× | 0.96× |
| `isolated.sorted_list.pop.indexed` | 0.99× | 0.98× | 0.98× | 0.96× |
| `isolated.sorted_list.delitem.indexed` | 0.94× | 0.89× | 0.96× | 0.96× |
| `slice.sorted_list.prefix.fraction.indexed` | 14.79× | 16.83× | 304.75× | 297.00× |
| `slice.sorted_list.suffix.fraction.indexed` | 57.43× | 79.25× | 306.32× | 286.99× |
| `slice.sorted_list.prefix.aligned.indexed` | 89.35× | 117.28× | 300.28× | 298.21× |
| `slice.sorted_list.prefix.partial_kept_large.indexed` | 22.10× | 24.10× | 213.91× | 190.90× |
| `slice.sorted_list.prefix.partial_underflow.indexed` | 12.35× | 11.91× | 156.74× | 152.95× |
| `slice.sorted_list.suffix.aligned.indexed` | 142.69× | 137.29× | 293.43× | 284.72× |
| `slice.sorted_list.suffix.partial_kept_large.indexed` | 21.60× | 20.49× | 152.76× | 151.91× |
| `slice.sorted_list.suffix.partial_underflow.indexed` | 8.85× | 8.67× | 81.95× | 83.38× |
| `append.sorted_list.update_tuple.unindexed` | 11.52× | 8.86× | 14.55× | 12.82× |
| `append.sorted_list.update_list.unindexed` | 11.82× | 10.55× | 13.92× | 13.58× |
| `append.sorted_list.update_tuple.indexed` | 21.04× | 15.19× | 16.34× | 15.37× |
| `append.sorted_list.update_list.indexed` | 19.93× | 15.06× | 15.55× | 15.27× |
| `slice.sorted_list.suffix.exclusive_ids.unindexed` | 44.17× | 34.37× | 79.08× | 105.43× |
| `slice.sorted_list.suffix.exclusive_ids.indexed` | 42.16× | 37.80× | 86.80× | 101.57× |
| `slice.sorted_list.prefix.exclusive_ids.unindexed` | 15.05× | 14.52× | 95.77× | 112.06× |
| `slice.sorted_list.prefix.exclusive_ids.indexed` | 14.69× | 14.05× | 84.29× | 106.83× |
| `batch.sorted_list.repeated_add` | 1.03× | 1.03× | — | — |
| `batch.sorted_list.update` | 0.92× | 0.87× | — | — |
| `batch.sorted_list.repeated_pop` | 1.01× | 0.98× | — | — |
| `batch.sorted_list.delete_slice` | 15.13× | 17.18× | — | — |
| `batch.sorted_set.repeated_discard` | 0.89× | 0.96× | — | — |
| `batch.sorted_set.difference_update` | 0.95× | 0.97× | — | — |

Small collection controls:

| Case | Linux 128 | macOS 128 | Linux 4k | macOS 4k |
| --- | ---: | ---: | ---: | ---: |
| `steady.sorted_list.random_replace.unindexed` | 1.05× | 0.98× | 1.03× | 1.03× |
| `steady.sorted_list.rolling_expiration.unindexed` | 1.00× | 1.00× | 1.07× | 0.99× |
| `steady.sorted_list.random_replace.indexed` | 1.00× | 0.99× | 1.00× | 1.04× |
| `steady.sorted_list.rolling_expiration.indexed` | 1.01× | 1.03× | 1.02× | 0.99× |
| `steady.sorted_set.random_replace.unindexed` | 1.02× | 1.01× | 1.02× | 1.01× |
| `steady.sorted_set.rolling_expiration.unindexed` | 1.02× | 1.00× | 1.00× | 1.00× |
| `steady.sorted_set.random_replace.indexed` | 0.97× | 0.93× | 1.01× | 1.01× |
| `steady.sorted_set.rolling_expiration.indexed` | 1.00× | 0.94× | 1.01× | 1.00× |
| `steady.sorted_dict.random_replace.unindexed` | 1.01× | 0.84× | 1.00× | 0.95× |
| `steady.sorted_dict.random_replace.indexed` | 0.96× | 0.86× | 1.03× | 0.98× |
| `fallback.custom_comparisons.random_replace` | 1.01× | 0.98× | 0.98× | 1.00× |
| `fallback.keyed_records.random_replace` | 1.02× | 0.99× | 1.05× | 1.05× |
| `isolated.sorted_list.add.unindexed` | 1.05× | 0.97× | 0.98× | 1.04× |
| `isolated.sorted_list.remove.unindexed` | 1.01× | 0.99× | 1.00× | 1.03× |
| `isolated.sorted_list.discard.unindexed` | 0.98× | 1.02× | 1.05× | 0.97× |
| `isolated.sorted_list.pop.unindexed` | 0.99× | 0.96× | 0.95× | 0.98× |
| `isolated.sorted_list.delitem.unindexed` | 0.82× | 0.73× | 0.89× | 0.86× |
| `slice.sorted_list.prefix.fraction.unindexed` | 0.91× | 0.85× | 0.96× | 0.93× |
| `slice.sorted_list.suffix.fraction.unindexed` | 0.87× | 0.84× | 1.14× | 1.13× |
| `isolated.sorted_list.add.indexed` | 1.03× | 0.96× | 1.03× | 1.02× |
| `isolated.sorted_list.remove.indexed` | 1.00× | 0.97× | 1.01× | 0.97× |
| `isolated.sorted_list.discard.indexed` | 1.00× | 0.96× | 0.96× | 1.01× |
| `isolated.sorted_list.pop.indexed` | 0.98× | 0.94× | 0.97× | 1.02× |
| `isolated.sorted_list.delitem.indexed` | 0.67× | 0.75× | 0.84× | 0.85× |
| `slice.sorted_list.prefix.fraction.indexed` | 0.85× | 0.93× | 0.99× | 0.96× |
| `slice.sorted_list.suffix.fraction.indexed` | 0.83× | 0.86× | 1.15× | 1.15× |
| `append.sorted_list.update_tuple.unindexed` | 0.92× | 0.93× | 3.59× | 3.50× |
| `append.sorted_list.update_list.unindexed` | 0.92× | 0.91× | 4.29× | 4.09× |
| `append.sorted_list.update_tuple.indexed` | 0.90× | 0.92× | 7.97× | 8.62× |
| `append.sorted_list.update_list.indexed` | 0.92× | 0.93× | 8.25× | 7.80× |
| `slice.sorted_list.suffix.exclusive_ids.unindexed` | 0.83× | 0.83× | 1.04× | 1.17× |
| `slice.sorted_list.suffix.exclusive_ids.indexed` | 0.83× | 0.84× | 1.11× | 1.13× |
| `slice.sorted_list.prefix.exclusive_ids.unindexed` | 0.86× | 0.85× | 0.98× | 0.98× |
| `slice.sorted_list.prefix.exclusive_ids.indexed` | 0.83× | 0.87× | 1.04× | 1.00× |
| `batch.sorted_list.repeated_add` | 1.01× | 0.96× | 1.04× | 1.02× |
| `batch.sorted_list.update` | 0.96× | 0.95× | 0.85× | 0.92× |
| `batch.sorted_list.repeated_pop` | 0.86× | 0.98× | 1.00× | 1.01× |
| `batch.sorted_list.delete_slice` | 0.58× | 0.65× | 0.94× | 1.02× |
| `batch.sorted_set.repeated_discard` | 1.03× | 1.01× | 1.01× | 1.04× |
| `batch.sorted_set.difference_update` | 1.00× | 0.95× | 1.03× | 1.01× |

Raw mutation samples: [linux 128](linux-128.json), [linux 4,096](linux-4096.json), [linux 65,536](linux-65536.json), [linux 1,000,000](linux-1000000.json), [macos 128](macos-128.json), [macos 4,096](macos-4096.json), [macos 65,536](macos-65536.json), [macos 1,000,000](macos-1000000.json).
