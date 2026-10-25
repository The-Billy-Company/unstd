# Process backend qualification for Billy

We audited 16 existing callsites across 14 Billy source files, then measured their command and capture shapes on macOS and Linux. macOS selects native process creation and captured output. Linux retains stdlib creation and selects native capture; its optimized launch path beat the C spawn prototype on ordinary Git metadata.

## Workloads and scope

The [frozen receipts](../../workloads/billy_proc_receipts.json) include source hashes, excerpts, argv/options and I/O contracts. Fixtures cover Git revision, NUL-delimited inventory, clean/dirty status, numstat, text blobs, pkg-config metadata, read-only process inventory and qualification logs. Git commands run only against disposable local repositories. The 1/16 MiB text sizes and log payloads are declared scaling fixtures; they are not production payload percentiles.

Current audited callers do not pass `stdin_text`. The stdin case is a synthetic fallback stress test and claims no Linux native acceleration. Binary asyncio/media/parser workers require different APIs and were not migrated. No Billy production commands or cleanup operations were executed.

## Paired results

Each platform ran 12 workloads over 11 paired rounds in one interpreter. Original and candidate source trees retain separate module objects and private backends; order alternates for each workload. Outputs are checked against independent fixtures outside timers. Profiles and successful native-route observations also run outside timers. Imports, fixture construction and warmups are excluded.

Times below are median milliseconds per complete call. Speedups divide the original median by the candidate median. Parent CPU excludes child CPU. Every paired sample, including losses, is retained; these shared-host measurements are not universal latency guarantees.

### macOS

macOS-26.6-arm64-arm-64bit-Mach-O; CPython 3.14.9.

| Workload | Before ms | Selected ms | Wall ratio | Parent CPU ratio |
| --- | ---: | ---: | ---: | ---: |
| `git-revision-cwd` | 14.048 | 10.983 | 1.28× | 5.00× |
| `git-revision-no-cwd` | 13.742 | 11.358 | 1.21× | 4.81× |
| `git-inventory-nul` | 13.516 | 11.563 | 1.17× | 3.12× |
| `git-status-clean` | 20.504 | 15.523 | 1.32× | 3.09× |
| `git-status-dirty` | 47.250 | 46.509 | 1.02× | 2.74× |
| `git-diff-numstat` | 26.210 | 19.441 | 1.35× | 3.21× |
| `git-show-1mib` | 23.312 | 18.767 | 1.24× | 2.17× |
| `git-show-16mib` | 86.620 | 66.290 | 1.31× | 1.34× |
| `pkg-config-libdir` | 6.207 | 2.844 | 2.18× | 8.42× |
| `ps-process-inventory` | 103.110 | 79.557 | 1.30× | 3.84× |
| `qualification-log-shape` | 38.865 | 23.428 | 1.66× | 2.52× |
| `stdin-text-stress-1mib` | 21.635 | 20.844 | 1.04× | 1.59× |

### Linux

Linux-7.0.14-linuxkit-aarch64-with-glibc2.41; CPython 3.14.8.

| Workload | Before ms | Selected ms | Wall ratio | Parent CPU ratio |
| --- | ---: | ---: | ---: | ---: |
| `git-revision-cwd` | 0.567 | 0.634 | 0.89× | 0.72× |
| `git-revision-no-cwd` | 0.500 | 0.509 | 0.98× | 1.12× |
| `git-inventory-nul` | 0.660 | 0.592 | 1.12× | 1.22× |
| `git-status-clean` | 0.552 | 0.558 | 0.99× | 1.13× |
| `git-status-dirty` | 24.194 | 24.172 | 1.00× | 1.13× |
| `git-diff-numstat` | 0.971 | 0.967 | 1.00× | 1.06× |
| `git-show-1mib` | 2.616 | 2.638 | 0.99× | 1.02× |
| `git-show-16mib` | 46.373 | 47.730 | 0.97× | 0.99× |
| `pkg-config-libdir` | 0.301 | 0.296 | 1.02× | 1.07× |
| `ps-process-inventory` | 0.923 | 0.944 | 0.98× | 1.03× |
| `qualification-log-shape` | 7.601 | 7.624 | 1.00× | 1.12× |
| `stdin-text-stress-1mib` | 1.822 | 1.824 | 1.00× | 1.00× |

Linux is a real ARM64 Linux/glibc kernel in a Docker Desktop VM on the same Mac, not bare-metal qualification. The two platforms are compared only against their own original source under the same interpreter.

Linux wall latency is generally near parity; modest CPU improvements do not imply a universal end-to-end win. The retained `git-revision-cwd` sample regressed by about 12% in wall time and 38% in parent CPU cost. Large child computation, Git decompression and text decoding can dominate pipe-loop changes. macOS shows clearer gains because its native spawn path avoids the prior fork overhead.

A later inline-buffer prototype passed sanitizers on both platforms but failed the performance acceptance check: gains varied across metadata paths and cwd metadata regressed. It was rejected; the selected implementation retains private heap buffers. Its [decision receipt](inline-buffer-rejected.json) records the source hash and confirms the qualified baseline stayed unchanged.

## Implementation and memory safety

The [backend](../../../src/unstd/proc/backend.py) keeps the public API unchanged. macOS uses isolated native spawn; Linux keeps stdlib spawn. Both admit the native output-only kernel and retain stdlib duplex communication for stdin. Stdlib still owns text decoding, universal newlines, SIGINT grace, waitpid synchronization and final reaping. Earlier pidfd/kqueue bounded waits remain enabled.

Encoded argv/environment entries use C-owned strong references. Callback-visible sequences are snapshotted before conversion; partially initialized result tuples stay hidden from cyclic GC. Empty executable searches never publish nonpositive PIDs. C publishes child ownership before returning through a signal checkpoint.

Captured output uses private checked RAW allocations and immutable-byte publication. Reallocation failures retain the original allocation for cleanup; initialized bytes alone are copied. Descriptor flags are snapshotted before changing either fd, including duplicated open-file descriptions, then restored on success, timeout and error. Cancellation frees buffers before Python kills and reaps the direct child.

The [isolated memory runner](../../../tests/tooling/runtime/proc_memory.py) rebuilds with strict warnings, AddressSanitizer and UndefinedBehaviorSanitizer. Both platforms passed 118 tests with no sanitizer findings, one platform-specific skip and two intentional exclusions. Debug allocators passed 119 tests on macOS and 120 on Linux; macOS/Python 3.12 passed 119. Adverse cases include callback mutation/GC discovery, exact UTF-8/newline and growth boundaries, partial timeout bytes, duplicated fds, SIGINT identity, descriptor stability, and real Linux address-space exhaustion followed by kill/reap/recovery.

The sanitizer runner restores loader/source selection in children. Linux stdlib fixture/oracle launches use fork because the uninstrumented CPython vfork controller is incompatible with this preload configuration; native C spawn remains exercised separately. Native-spawn boundary tests explicitly enable that private path on Linux even though production selects stdlib. Native capture is proved active through the default public API on both platforms.

The exact environment-isolation case runs in ordinary/debug tests; the sanitizer wrapper necessarily restores its loader variables. The real address-space-limit test runs outside ASan because ASan reserves virtual address ranges. LeakSanitizer is disabled for CPython/process-lifetime allocations; bounds, invalid access/free and UB checks remain fatal. These checks are evidence, not a mathematical proof that C has no defects.

CI runs the isolated proc qualifier on sanitizer shard 1 and retains its manifest, JUnit and logs. Build/publication checks include the optional extension, source files, ABI imports and installed base-consumer native calls. Missing compilers or unsupported facilities retain the stdlib fallback.

The final sanitizer receipts match the current flattened proc sources on both platforms. The final integration, benchmark provenance, workflow, memory-runner and public API gate selection passed all 146 tests; lint and Linux/macOS/Windows type checks passed for the production backend and controllers.

## Evidence and reproduction

- [macOS paired samples](billy-native-macos-final.json) and [Linux paired samples](billy-native-linux-final.json).
- [Exact qualified source/controller/binary snapshot](billy-native-final-snapshot.tar.gz) and [snapshot manifest](billy-native-final-snapshot-manifest.json).
- [macOS sanitizer manifest](native-memory-macos.json) and [Linux sanitizer manifest](native-memory-linux.json), with [macOS log](native-memory-macos.txt) and [Linux log](native-memory-linux.txt).
- [Original Python source](python-before-native.tar.gz), which already includes the prior exit-wait optimization.

Unpack the exact snapshot and use its controller/source paths to reproduce the recorded run. Live source reorganizations do not change that artifact. The current standalone drivers are `native/proc/experiments/paired.py` and `bench/profiles/billy_proc.py`; the latter also supports explicit native-spawn, native-capture, Python and stdlib controls.

```console
uv run --no-sync python tests/tooling/runtime/proc_memory.py --sanitize --output /tmp/proc-memory-audit
uv run --no-sync pytest tests/suites/product/proc -q
```
