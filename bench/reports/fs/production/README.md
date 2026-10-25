# Linux atomic-write production qualification

`unstd.fs.atomic_write` now dispatches admitted Linux writes through the bundled
`unstd.fs._fs_native` backend. Python remains the complete fallback. This is
public-API verification of built and installed packages, superseding the earlier
standalone prototype's cancellation and error limitations.

The evidence qualifies the retained package snapshot identified by
[`qualified_fs_runtime_sha256` in the manifest](manifest.json). These hashes
describe the wheel/sdist contents used for the recorded checks, rather than
tracking the latest checkout. Later source edits, including documentation-only
edits, do not rewrite those receipts or imply a new test/benchmark run. The
[public fs guide](../../../../src/unstd/fs/README.md) and
[native backend guide](../../../../native/fs/README.md) describe the implementation;
the [report index](../README.md) separates this stage from earlier experiments.

## Behavior

The native path accepts exact `bytes` and default UTF-8 `str` with an existing
parent directory. Encoding occurs after scratch acquisition. Other codecs,
subclasses, missing/non-directory parents, unavailable kernel entropy, and
custom OS/path/orchestration bindings use Python. Missing native binaries or
optional compiler failure also retain Python. No native I/O exception retries
through the fallback.

The kernel preserves private creation, existing modes, exclusive same-directory
scratch names, complete writes, file fsync, atomic rename, and optional directory
syncing. Signals are checked at syscall boundaries. Acquired descriptors are
owned before cancellation can raise, close is never retried on Linux, and
cleanup preserves the primary exception with secondary error notes. Pre-commit
cancellation retains the destination; successful rename is marked committed
before signal handling. Later cancellation/sync failures identify committed
state. The Python writer also checks retained inode identity after a rename
interrupted on return, fixing its previously missing committed-state note.

`getrandom` ENOSYS/EPERM declines before scratch creation, retaining CPython's
secure entropy fallback. Other entropy failures and cancellation propagate.
The binding guard uses identity; custom callable equality cannot impersonate
an original OS hook.

Normal Linux CPython builds include the extension. Non-Linux and unsupported
interpreter/build configurations retain Python. A strict release build fails
if compilation fails; optional local builds remain usable. `atomic_writer`
continues to provide the Python streaming interface. No Billy application code,
credentials, persistence policy, or public call signature was changed.

## Functional and packaging evidence

| Verification | Result |
| --- | --- |
| Linux CPython 3.12.15 | 236 passed |
| Linux CPython 3.13.16 | 236 passed |
| Linux CPython 3.14.8, installed direct wheel | 236 passed |
| Linux CPython 3.14.8, installed sdist-rebuilt wheel | 236 passed |
| ASan + UBSan, no skipped cases | 236 passed, zero diagnostics |
| Compiler-failure and pristine installed-consumer checks | 4 passed |

The suite includes 62 native contracts for actual public C routing, forced Python
fallback, pre/post-commit signals, UTF-8 failures, precise OS error fields,
strict preflight/post-sync boundaries, partial writes, EINTR, permissions,
cleanup failures, descriptor release, and unsupported native entropy. Existing
writer interruption, symlink-race, mode, and reader-atomicity contracts remain
in place. The build checks prove a missing compiler yields a portable fallback,
a failure of only the fs compiler leaves other accelerators usable, strict
release failure is visible, and pristine consumers exercise both native and
Python paths from each artifact route.

C builds on Python 3.12/3.13 pass `-Wall -Wextra -Werror`. Both final 3.14 package
routes were built with `CIBUILDWHEEL=1`, then installed without dependency extras.
The recorded wheels and sdist retain the qualified fs runtime, stub and C source
hashes in the manifest. The snapshot uses `fs.durability` and `fs.traversal`.
This is artifact provenance for that snapshot, not a claim that every later
workspace revision has been qualified.

After qualification, module/function docstrings in `src/unstd/fs/__init__.py`
were updated. The manifest separately records that source hash and an
AST comparison excluding docstrings against the retained direct wheel. Execution
statements matched; this review did not rebuild or rerun the artifacts.

The sanitizer run uses a documented test-only fork launcher because this
arm64 VM traps in preloaded-ASan vfork children, reproducibly without fs import.
Every filesystem child retains ASan-first plus the ELF observer, and all test
assertions remain unchanged. Compiler processes alone omit runtime preload.
The receipt retains the initial infrastructure diagnosis, actual tested byte
hashes, and an AST-equivalence review for subsequent empty-export declarations
in the helpers; it does not relabel untested bytes as tested.

- [Linux 3.12/3.13 matrix and hashes](python-matrix.json)
- [Installed direct-wheel tests](tests-installed-direct.txt)
- [Installed sdist-wheel tests](tests-installed-sdist.txt)
- [Packaging/fallback tests](packaging-fallback.txt)
- [ASan/UBSan receipt](sanitizers.json) and [test output](sanitizer-tests.txt)
- [Strict direct build](build-direct.txt) and [strict sdist rebuild](build-sdist.txt)

## Billy workloads through the installed public API

The [installed driver](../../../profiles/billy_fs_installed.py) loads two public
fs modules from the same installation. One retains native dispatch; the other
sets `_native=None`. Every lane calls public `fs.atomic_write`. The selected
wheel's ZIP members must match every loaded fs Python file and compiled binary.

All 13 captured workload shapes passed for the direct wheel and sdist-rebuilt
wheel on disk, plus the direct wheel on tmpfs: 39 complete workload runs. Before
and after every workload, independent profile hooks prove native C calls with
zero Python fallbacks, and forced-Python calls with zero native entries.
Sentinel resets, exact bytes, private modes, and no retained scratch files are
checked outside timing. A no-op, declined route, wrong binary, or quiet fallback
cannot support the result. All sources, inputs, wheels and binaries are checked
again after timing.

The [synthetic fixtures](../../../workloads/billy_fs.json) retain source receipts
for token/config/report writes, browser state, and notebook checkpoints. Browser
and notebook reference functions use checked Billy ASTs; quota checks remain in
the complete browser operation. No token, cookie, or notebook data was captured.
The report-serialization control uses common stdlib JSON; actual jsonx codec
latency is excluded. Browser/notebook reference writers reset mode while
candidates preserve it; all test destinations are 0600. Strict notebook candidates
add preflight syncing. These retain their earlier migration boundaries.

Ratios below are **Python fallback time / native public-API time**. Above 1 means
faster native work. Raw losses remain visible; CPU and wall latency are separate.

| Workload | Direct wheel disk wall / CPU | Sdist wheel disk wall / CPU | Direct wheel tmpfs wall / CPU |
| --- | ---: | ---: | ---: |
| oauth-token | 1.07 / 1.43 | 1.02 / 1.35 | 1.46 / 1.46 |
| import-config | 1.00 / 1.48 | 1.13 / 1.32 | 1.55 / 1.55 |
| capture-report | 0.75 / 1.06 | 0.99 / 1.15 | 1.21 / 1.21 |
| browser-write-4096 | 1.06 / 1.53 | 1.08 / 1.28 | 1.79 / 1.79 |
| browser-write-65536 | 0.91 / 1.30 | 1.45 / 1.44 | 1.37 / 1.37 |
| browser-write-1048576 | 1.01 / 1.24 | 1.11 / 1.23 | 1.04 / 1.04 |
| browser-write-4194176 | 1.04 / 1.04 | 0.93 / 1.04 | 1.02 / 1.02 |
| browser-land-65536-peers-0 | 0.89 / 1.05 | 1.13 / 1.18 | 1.25 / 1.25 |
| browser-land-65536-peers-64 | 0.89 / 1.03 | 0.88 / 1.07 | 1.05 / 1.05 |
| browser-land-65536-peers-512 | 1.29 / 1.10 | 1.07 / 1.07 | 1.02 / 1.02 |
| notebook-checkpoint-4096 | 1.08 / 1.48 | 0.98 / 1.08 | 1.66 / 1.66 |
| notebook-checkpoint-1048576 | 1.05 / 1.13 | 0.70 / 1.02 | 1.05 / 1.05 |
| notebook-checkpoint-18874368 | 1.02 / 1.07 | 0.80 / 1.07 | 1.04 / 1.04 |

Small writes retain useful CPU gains through production dispatch. Disk latency
remains variable, and complete cache scans/large checkpoints dilute the gain.
No uniform service-latency claim or frozen speed-floor change is made. tmpfs
isolates CPU/VFS overhead and does not establish persistent-storage performance.

All measurements used Linux arm64 containers on a Docker Desktop VM, CPython
3.14.8, GCC 14.2, real fsync, shared mutation paths, alternating order, nine rounds,
and shared batches calibrated toward 20 ms. Calibration, route proofs, output
checks and separate cProfile runs are outside timed samples; normal GC stays
active. This is not production-device or x86_64 latency certification.

- [Installed direct wheel, disk](installed-direct-overlay.json)
- [Installed sdist wheel, disk](installed-sdist-overlay.json)
- [Installed direct wheel, tmpfs](installed-direct-tmpfs.json)
- [Artifact/source manifest](manifest.json)

The manifest identifies retained local wheel/sdist artifacts and their hashes.
The wheels are the exact binaries used by the public routing and workload checks.
Its source map is named `qualified_fs_runtime_sha256`; recorded report and
artifact hashes remain unchanged when documentation is updated.

## Reproduction

Inside Linux, build the wheel/sdist and independently rebuild the sdist, then
install each wheel into a fresh target. Run the fs suite using the chosen
installation. The ELF-boundary cases additionally need a C compiler and the
repository's `native/fs/qualification/observe.c`.

Use the same interpreter and test dependencies for both routes. A build from a
new checkout creates new evidence; it reproduces the retained qualified snapshot
only if its source and artifact identities match the manifest.

```console
CIBUILDWHEEL=1 python -m build --wheel --sdist --no-isolation --outdir /tmp/fs-dist
python -m pip install --no-deps --target /tmp/fs-install /tmp/fs-dist/unstd-3.1.6-cp314-cp314-linux_aarch64.whl
PYTHONPATH=/tmp/fs-install python -m pytest tests/suites/product/fs -q
python bench/profiles/billy_fs_installed.py --source /tmp/fs-install --wheel /tmp/fs-dist/unstd-3.1.6-cp314-cp314-linux_aarch64.whl --fixtures bench/workloads/billy_fs.json --billy-source ../billy --root /tmp --rounds 9 --output /tmp/fs-installed.json
```

Use the built wheel's actual filename for another ABI/platform. Repeat with the
sdist-rebuilt installation:

```console
mkdir -p /tmp/fs-rebuild-source
tar -xzf /tmp/fs-dist/unstd-3.1.6.tar.gz -C /tmp/fs-rebuild-source
CIBUILDWHEEL=1 python -m build --wheel --no-isolation --outdir /tmp/fs-sdist-dist /tmp/fs-rebuild-source/unstd-3.1.6
python -m pip install --no-deps --target /tmp/fs-sdist-install /tmp/fs-sdist-dist/unstd-3.1.6-cp314-cp314-linux_aarch64.whl
PYTHONPATH=/tmp/fs-sdist-install python -m pytest tests/suites/product/fs -q
python bench/profiles/billy_fs_installed.py --source /tmp/fs-sdist-install --wheel /tmp/fs-sdist-dist/unstd-3.1.6-cp314-cp314-linux_aarch64.whl --fixtures bench/workloads/billy_fs.json --billy-source ../billy --root /tmp --rounds 9 --output /tmp/fs-sdist-installed.json
```

Use `--root /dev/shm` only for the RAM diagnostic. The matching Billy source
receipts are required for either route; changed consumer files fail verification
instead of silently changing the workload. Repeat `--include NAME` for focused
follow-ups and retain them separately from complete runs.

The historical experiment driver explicitly forces its Python lane to fallback
so production default dispatch cannot silently change that older comparison.
