# Filesystem backend evaluation

The [production qualification](production/README.md) records the installed Linux
backend, Python fallback, cancellation/errors, packaging, and checked Consumer
workloads. Its [manifest](production/manifest.json) identifies the qualified
source snapshot and retained direct-wheel/sdist artifacts. Later workspace
edits do not update that evidence.

| Stage | Evidence | Scope |
| --- | --- | --- |
| Installed Linux backend | [Production qualification](production/README.md) | Public native dispatch versus forced Python fallback from identical installed packages; direct wheel, sdist rebuild, signals/errors, compiler fallback and sanitizers |
| Standalone Linux prototype | [Historical Consumer evaluation](consumer-linux.md) | Explicit `_fs_experiment` binary; original cancellation/error limitations, synthetic consumer shapes and separate reference lanes |
| Python orchestration | Results below and [Linux follow-up](backend-linux-py314.json) | Initial traversal/directory/writer changes, before production native dispatch |

The historical measurements below describe the initial Python optimization on
macOS. They do not measure the installed native backend or the latest checkout.

Measured October 25, 2026 on macOS arm64 with CPython 3.12.15, 3.13.17,
and 3.14.9. This is local warm-cache evidence; storage behavior and scheduling
vary substantially between samples.

## Historical Python implementation and decisions

The descriptor walker retained `O_NOFOLLOW`, parent-relative child opens, lazy
iteration, and handles bounded by traversal depth. That snapshot cached string
prefixes, constructed `Path` objects only for yielded files, and checked the
common file case first. Explicit scanner/descriptor ownership replaced two
`ExitStack` allocations per directory. A final cleanup stack still released
every live ancestor when iteration or a close failed.

`ensure_dir` checked an existing directory before attempting `mkdir`. This
avoided a failed syscall and exception on the common idempotent path. Strict
parent preparation reused its successful directory stat instead of attempting
creation again. Creating a missing directory paid for an initial stat; the
create/remove workload below retains that cost.

No file sync, directory-sync preflight, permission copying, atomic rename, or
rollback step was removed. The portable walker kept every identity recheck;
the explicitly following walker was unchanged. This stage changed Python
orchestration only, adding no compiled backend or third-party runtime dependency.

## Results

Each number is **before time / candidate-snapshot time**; above 1 means faster
candidate code. Wall latency and process CPU are shown separately. Full tables
retain losses and unchanged-code controls, rather than asserting a uniform gain.

| Workload | Python 3.12 wall / CPU | Python 3.13 wall / CPU | Python 3.14 wall / CPU |
| --- | ---: | ---: | ---: |
| walk-flat | 1.05 / 1.05 | 1.18 / 1.11 | 1.10 / 1.08 |
| walk-nested | 1.26 / 1.13 | 1.14 / 1.10 | 0.89 / 0.99 |
| walk-empty-directories | 1.09 / 1.10 | 0.74 / 0.97 | 1.47 / 1.37 |
| walk-deep | 1.50 / 1.39 | 1.05 / 1.05 | 0.90 / 1.07 |
| walk-portable | 0.92 / 0.96 | 0.90 / 1.06 | 1.11 / 0.97 |
| walk-early-close | 1.43 / 1.36 | 1.15 / 1.08 | 0.77 / 1.02 |
| walk-follow-symlinks | 0.98 / 1.06 | 0.96 / 0.96 | 0.99 / 0.96 |
| atomic-write-bytes-1k | 1.07 / 1.10 | 0.70 / 0.86 | 0.78 / 0.91 |
| atomic-write-text-1k | 0.77 / 0.98 | 0.98 / 1.04 | 1.00 / 1.03 |
| atomic-write-bytes-1m | 0.92 / 0.95 | 1.30 / 0.99 | 1.06 / 1.04 |
| atomic-write-durable | 0.83 / 0.96 | 1.01 / 1.02 | 0.82 / 1.04 |
| atomic-write-strict | 1.09 / 1.08 | 1.02 / 1.10 | 0.92 / 0.99 |
| atomic-writer | 1.16 / 1.06 | 1.42 / 1.04 | 1.28 / 1.10 |
| ensure-dir-existing | 1.45 / 1.46 | 1.40 / 1.42 | 1.40 / 1.42 |
| ensure-dir-create-remove | 0.90 / 0.94 | 0.97 / 0.93 | 0.96 / 0.95 |
| atomic-replace-pair | 0.88 / 0.97 | 1.14 / 1.04 | 1.00 / 1.00 |
| sync-dir | 0.98 / 0.99 | 1.01 / 1.02 | 1.01 / 1.01 |

Existing-directory checks improved consistently by **1.40–1.45× in wall time**
and **1.42–1.46× in process CPU**. Flat traversal used about **5–10% less
process CPU**. Nested and directory-heavy traversal varied by interpreter and
sample; some wall measurements regressed even when CPU improved. No uniform
traversal latency or atomic-write speedup is established. The missing-directory
create/remove cycle cost about **3–11% more wall time** in these full runs.

Real I/O is noisy here: unchanged portable/following walks and atomic replacement
also moved in both directions. For example, the Python 3.14 deep-walk wall
samples spanned roughly 2–7 ms per operation. These results do not justify
changing frozen speed ceilings or claiming broader platform improvements.

## Longer Python 3.14 follow-up

The variable full-run results prompted a separate 31-round comparison, starting
at 100 repetitions per batch (multiplied by each workload's batch size). This
retains slower cases and does not replace the complete run above.

| Workload | Wall ratio | CPU ratio |
| --- | ---: | ---: |
| walk-nested | 1.07 | 1.07 |
| walk-empty-directories | 1.14 | 1.09 |
| walk-early-close | 1.03 | 1.05 |
| atomic-write-bytes-1k | 1.15 | 1.04 |
| atomic-write-text-1k | 0.89 | 0.98 |
| atomic-write-durable | 0.99 | 1.04 |
| ensure-dir-create-remove | 0.95 | 0.95 |

The longer run supports modest traversal improvements and confirms the
missing-directory creation tradeoff. Atomic timings still disagree by workload;
there is no basis for a package-wide write-speed claim. Raw samples and profiles
are in the [follow-up report](backend-followup-macos-py314.json).

## Method and retained evidence

The [driver](../../profiles/fs_backend.py) loads both complete fs packages from
source into separate module objects, binds matching internals, rejects stale
bytecode, checks all public signatures, and restores existing imports. All
expected files and contents are defined independently of the measured walks.
Outputs are checked before timing and after each batch. Source and harness
hashes are verified again before publishing evidence.

The three complete runs use 11 rounds with alternating source order and shared
batch counts calibrated toward at least 20 ms, with calibration capped at
10,000 operations. Calibration and separate cProfile runs are excluded from
timed samples. GC retains its normal state. Mutating workloads share exactly the
same paths between implementations. Writes and directory syncs use real OS
calls; no mocked syncing supports these ratios. The create/remove case includes
`rmdir`, and the rename-pair case includes two renames to restore its fixture.

- [Python 3.12 raw timings and profiles](backend-macos-py312.json)
- [Python 3.13 raw timings and profiles](backend-macos-py313.json)
- [Python 3.14 raw timings and profiles](backend-macos-py314.json)
- [Original source snapshot](backend-before.tar.gz)

Every JSON file retains source and harness SHA-256 hashes, interpreter and
platform details, calibration, operation counts, sample order, every wall/CPU
sample, median timings, and the top profile entries. The snapshot contains the
four original runtime Python files, and its contents were checked against all
three reports' baseline hashes.

To rerun the comparison driver from the repository root:

```console
mkdir -p /tmp/unstd-fs-before
tar -xzf bench/reports/fs/backend-before.tar.gz -C /tmp/unstd-fs-before
python3.14 bench/profiles/fs_backend.py --compare-source /tmp/unstd-fs-before --source src --rounds 11 --repetitions 3 --output /tmp/fs-py314.json
```

Repeat with Python 3.12 and 3.13. The direct entry point needs no optional
packages. `--include NAME` selects a workload and can be repeated. Absolute
paths in retained profiles describe the original run; hashes identify source
contents after extraction to another location. The archive retains the original
baseline. `--source src` selects the checkout at rerun time; matching the original
candidate requires the source bytes identified in the report. A new run neither
reproduces those candidate bytes automatically nor replaces the historical data.

## Validation

At this stage, all **163 fs product tests** passed on Python 3.12, 3.13, and 3.14.
The Python 3.14 combined run passed **177 tests**, including 14 harness contracts. Two
mixed atomic/strict-durability fault histories passed separately. Ruff lint and
formatting and strict mypy passed for the fs runtime and benchmark driver.

New product cases cover existing-file and symlink directory-helper behavior,
nested scanner iteration/close failures, release of all acquired scanners and
descriptors, lazy acquisition, Unicode names, and relative paths. Existing
permission, symlink-race, writer interruption, reader atomicity, rollback,
strict-sync preflight, and post-commit failure tests remain in place.

The initial portable branch checks ran on macOS. The subsequent Linux follow-up
passed all 163 fs product tests and retained a [Linux Python comparison](backend-linux-py314.json).
Windows execution was not performed. The subsequent installed native
qualification and its larger contract suite are recorded separately in
[production/](production/README.md).
