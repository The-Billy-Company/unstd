# Filesystem operations: Linux native evaluation

The [production follow-up](production/README.md) completes cancellation/error
qualification and verifies installed public dispatch for its recorded package
snapshot. This page retains the earlier experimental measurements and their
original admission limits. The [report index](README.md) distinguishes all three
stages; prototype ratios below do not describe the shipped public backend.

A native one-shot writer can reduce CPU for small atomic writes on Linux.
The evidence does not establish a broad server latency improvement:
large checkpoints gain little, disk timings vary, and browser cache scanning
can dominate the complete operation. This standalone `_fs_experiment` proof
remains unshipped; production uses the separately qualified `_fs_native` backend.

Measured October 25, 2026, in an isolated **Linux aarch64 Docker Desktop VM**,
CPython **3.14.8**, GCC **14.2.0**, with two CPUs of quota and a 2 GiB memory
limit. The storage paths were container overlay storage over the VM disk and
`/dev/shm` tmpfs. These are Linux measurements, not production-device or x86_64
certification. tmpfs shows CPU/VFS headroom and provides no persistence claim.

## Captured consumer operations

At capture time, three direct `unstd.fs` calls were found in the consumer Python code, all
`atomic_write` in tooling:

| Consumer | Captured operation |
| --- | --- |
| `services/ai/scripts/auth/google_oauth.py:106` | Refresh-token text, followed by chmod 0600; synthetic 128-byte token |
| `services/ai/scripts/lint/refresh_import_baseline.py:251` | Text config rewrite; source-sized 47,473-byte synthetic config |
| `tools/igor/connectors/storage/capture/probe.py:494` | Sorted, indented JSON snapshot; synthetic leaf values over the bundled report schema, 14,112 encoded bytes |

The server candidates are prospective migrations:

- **Browser state:** `services/ai/tools/system/control/browser/hosted/custody.py:234`
  encodes UTF-8, enforces 4 MiB per state and 64 MiB total, scans other
  `*/state.json` files, then privately writes, flushes, fsyncs, and replaces.
  Scenarios cover 4 KiB, 64 KiB, 1 MiB, and just under 4 MiB, plus complete landing
  with 0, 64, or 512 peer profiles. Peer fixtures are 128 bytes each; this tests
  metadata-scan scaling, not an observed state-size distribution.
- **Notebook persistence:** the notebook policy at line 73
  writes bytes, fsyncs the file, replaces, and requires a parent-directory
  fsync. Retention consumers include receipts, checkpoints, source snapshots,
  originals, documents, identity, and history. Periodic checkpoints run at most
  once per second. Scenarios cover 4 KiB, 1 MiB, and the 18 MiB notebook ceiling.

No direct `atomic_writer`, `atomic_replace`, `ensure_dir`, `sync_dir`, or
`walk_files` use was found. The application also traverses files through other APIs:
Bridge listings additionally need pruning, sorting, metadata, or deadlines,
and the sandbox session walker is already Rust. A generic native walker is
not justified by the current fs call sites.

Bridge `services/bridge/core/path.py:475` requires owner/group and Linux xattr
preservation, symlink/hardlink/special-file rejection, exclusive publication of
new names, and conflict checks for rewrites. This proof does not implement that
contract. Gzip journal append and cached embedding reads also are not one-shot
atomic-write workloads.

The captured [filesystem fixture](../../workloads/consumer_fs.json) retains exact consumer symbol/file
hashes and input-file receipts. All payloads are synthetic; no token, cookie,
user notebook, or local login state was captured. Sizes are sensitivity cases,
not production-frequency weights. Full-file hashes identify the captured
working-tree sources independently of concurrent unrelated application commits.

## Native proof and fair comparison

The [C proof](../../../native/fs/experiments/README.md) performs parent checks
and creation, mode-preserving exclusive scratch creation, complete writes,
real file fsync, close, rename, cleanup, and optional best-effort/strict
parent syncing. It releases the GIL during blocking filesystem work, following
[CPython's extension guidance](https://docs.python.org/3.14/c-api/threads.html#detaching-the-thread-state-from-extension-code).
It uses the same scratch-name length budget as Python and never changes umask.

Both timed wrappers include UTF-8 encoding where required and return a `Path`.
Isolated browser cases encode before both binary writers, matching the source
operation. Complete browser cases compile the captured `land_state` AST and
replace only its publication block, retaining its encoding and budget scan.
The captured original browser and notebook functions also run as reference
lanes without importing application dependencies.

Every destination is initially 0600. Original browser/notebook writers reset
replacement mode to 0600; `unstd` and the prototype preserve existing modes.
Notebook candidate lanes use `durable="strict"`, adding a preflight sync to
the original post-commit directory-sync contract. Consequently those lanes
are migration experiments, rather than claims of full drop-in parity.

The report serialization control uses identical stdlib-compatible JSON for
both backends. Actual `jsonx` codec latency is not measured; the filesystem
component and resulting byte size are controlled.

## Measured results

Ratios are **captured Python-snapshot time / prototype-native time**. Above 1 means faster native
code. Wall time and process CPU remain separate. These complete 13-round runs
retain losses, including noisy disk cases.

| Workload | Overlay wall / CPU | tmpfs wall / CPU |
| --- | ---: | ---: |
| oauth-token | 1.53 / 1.72 | 2.60 / 2.60 |
| import-config | 0.98 / 1.37 | 1.95 / 1.95 |
| capture-report | 1.32 / 1.85 | 1.29 / 1.29 |
| browser-write-4096 | 0.99 / 1.64 | 3.03 / 3.03 |
| browser-write-65536 | 1.43 / 1.49 | 1.78 / 1.79 |
| browser-write-1048576 | 1.11 / 1.12 | 1.16 / 1.16 |
| browser-write-4194176 | 1.07 / 1.14 | 1.08 / 1.08 |
| browser-land-65536-peers-0 | 0.87 / 1.23 | 1.58 / 1.58 |
| browser-land-65536-peers-64 | 0.83 / 1.00 | 1.11 / 1.11 |
| browser-land-65536-peers-512 | 1.08 / 1.06 | 1.02 / 1.02 |
| notebook-checkpoint-4096 | 0.63 / 1.09 | 2.41 / 2.41 |
| notebook-checkpoint-1048576 | 1.23 / 1.29 | 1.11 / 1.11 |
| notebook-checkpoint-18874368 | 1.02 / 0.99 | 1.05 / 1.05 |

Small-write CPU savings were substantial in several cases, while 1–18 MiB
payloads diluted the orchestration savings. tmpfs small writes improved by
about 1.8–3.0× in selected token/config/browser/checkpoint cases, but this
measures a filesystem without persistent-device flushing. The complete
512-profile browser operation improved only about 2% on tmpfs, despite faster
individual writes.

Disk results include contradictory latency samples, so no uniform disk-write
speed claim is supported. For example, the 4 KiB strict checkpoint measured
0.63× wall despite 1.09× CPU in the complete run. A longer comparison was
therefore retained separately, with 31 alternating rounds and 10 initial
repetitions per batch:

| Workload | Wall ratio | CPU ratio |
| --- | ---: | ---: |
| oauth-token | 1.99 | 2.35 |
| import-config | 1.20 | 1.59 |
| browser-write-65536 | 1.02 | 1.07 |
| browser-land-65536-peers-64 | 1.20 | 1.09 |
| browser-land-65536-peers-512 | 1.03 | 1.10 |
| notebook-checkpoint-4096 | 0.97 | 1.32 |

The longer run retains a CPU benefit for small token/config/checkpoint
operations. Browser 64 KiB isolated-write savings were modest on that storage
run; the complete cache-scan cases had CPU ratios of 1.09–1.10×.
The 4 KiB checkpoint still had no wall improvement. The 18 MiB checkpoint in the
complete run measured 1.02× wall and 0.99× CPU: no meaningful native gain.

Comparison with the **actual captured consumer routines**, rather than the current
Python fs API, is also retained for the longer run. These reference lanes carry
the mode and strict-preflight differences explained above:

| Reference operation | Consumer/native wall ratio | Consumer/native CPU ratio |
| --- | ---: | ---: |
| browser-land-65536-peers-64 | 0.87 | 0.98 |
| browser-land-65536-peers-512 | 1.09 | 1.10 |
| notebook-checkpoint-4096 | 0.90 | 1.19 |

These results support targeting one-shot small writes if their aggregate CPU
cost becomes material. They do not justify migrating Bridge with weaker
semantics or claiming a general browser/checkpoint service speedup. Current
direct fs users are tooling, and no production frequency or weighted service
CPU measurements were supplied.

Separate cProfile entries show where the remaining work sits. At 512 profiles,
the browser budget sum and pathlib traversal dominate the profile; a native
commit cannot remove that work. Atomic-write profiles retain real fsync cost.
File-content fsync and directory-entry syncing remain separate obligations,
as described by the [Linux fsync manual](https://man7.org/linux/man-pages/man2/fsync.2.html).
No sync was skipped, replaced with fdatasync, or amortized across commits.

## Validation and production boundary

The existing **163 fs product tests passed on Linux**. The native proof passed
**14 Linux qualification tests**, including real sync/rename ordering,
strict preflight failure, post-commit failure notes, new-parent acknowledgments,
short writes, EINTR, permissions, Unicode and long names, descriptor bounds,
cleanup, and concurrent reader atomicity. The 14 tests were rerun for the
retained [qualification receipt](native-linux-tests.txt).

The combined product/harness suite passed **189 tests** on the development
host, including 26 benchmark contracts. Ruff lint/formatting and strict mypy
passed for the relevant runtime, drivers, and Python qualification tools.
Harness tests reject source/payload drift, wrong results, unsafe modes,
non-native/declined entrypoints, and no-op native calls borrowing old output.
Untimed sentinel resets precede every measured batch. The ELF observer is used
only in isolated qualification children and is absent during timing.

The proof is outside `hatch_build.py` and public dispatch. It handles Python
signals after its C transaction, so cancellation can be reported after commit.
That timing differs from Python's intermediate cancellation checkpoints.
Complete argument/error-note parity, allocator faults, and additional platform
qualification were left for later production work. Public fs dispatch and
consumer files were not changed for this experiment. The
[production qualification](production/README.md) records the later implementation,
including syscall-boundary cancellation, fallback and installed-package proof;
its results do not retroactively change this prototype's behavior or ratios.

## Reproduction and retained evidence

The direct [experiment driver](../../profiles/consumer_fs.py) needs only stdlib plus the
explicitly built experiment. Checked consumer source files are required for
reference ASTs and drift checks; application dependencies are not imported.
Run inside Linux, using the extension filename printed by the build:

```console
python native/fs/experiments/build.py --output-dir /tmp/fs-proof --observer
python native/fs/experiments/qualify.py --extension-dir /tmp/fs-proof --observer /tmp/fs-proof/observe.so
python bench/profiles/consumer_fs.py run --fixtures bench/workloads/consumer_fs.json --consumer-source ../consumer --source src --native-extension /tmp/fs-proof/_fs_experiment.cpython-314-aarch64-linux-gnu.so --root /tmp --rounds 13 --output /tmp/fs-experiment.json
```

Use the built extension's actual filename. The driver forces its Python lane to
fallback even when the selected source has production native dispatch. These
commands measure the source/driver selected at rerun time; exact historical
reproduction requires the hashes in the retained reports and manifest.
Use `--root /dev/shm` for the tmpfs diagnostic, or `--python-only` for source
profiles without an extension. `--include NAME` selects an exact workload.
Regenerate fixtures deliberately with the `fixtures` subcommand when consumer
source changes; drift does not silently update the captured contracts.

The raw measurements remain separate from the later installed-wheel evidence:

- [Complete disk-backed overlay run](consumer-native-linux-overlay-py314.json)
- [Complete tmpfs run](consumer-native-linux-tmpfs-py314.json)
- [Longer overlay follow-up](consumer-native-linux-overlay-followup-py314.json)
- [Linux environment](native-linux-environment.json) and [build/source manifest](native-linux-manifest.json)
- [Prior Python optimization measured on Linux](backend-linux-py314.json)

Timings use shared mutation paths, alternating implementation order, and shared
batch calibration toward 20 ms, capped at 10,000 operations. Checks, calibration,
and cProfile are excluded from retained timed samples; normal GC remains
active. Every report retains raw wall/CPU samples, medians, counts, source and
binary hashes, and top profile entries. Inputs and binaries are verified again
after timing. The native binary hash is tied to the qualified C source in the
manifest; no ambient native import or Python fallback supports the claim.
