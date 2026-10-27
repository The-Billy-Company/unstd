# Cache validation

## Current typed-artifact validation

The measured cache source SHA256 is
`3ff4aecc4cc8fd68807de62fcec1125cb58e20cb4e7b0a0e92241dfcad8655bd`;
locking is `eab1a7949bcde294e81834e34fa4947b6190099652b7aaf6092b2044c6ade19c`.
The [current workflow report](hot-paths.md) and [numeric receipt](hot-paths.json)
retain final paired wall samples, intermediate cold losses, exact-output checks,
source identities, and explicit private-source reproduction limits.

- **144 cache tests** pass on macOS and in each installed Linux native and
  no-extras/no-native environment. The current cache plus benchmark-coverage
  selection passes **182 tests** (144 + 38).
- **77 native caller tests** pass on both platforms with zero failures, errors,
  or skips. This is a source-backed private-caller cohort; public package drivers
  do not reproduce that application.
- Strict mypy passes six cache/driver files and two Windows-targeted files.
  Scoped caller Pyright reports zero diagnostics across three production and two
  test files. Ruff and formatting pass 17 cache and six caller files; the scoped
  diff check is clean. The 12 cache workload correctness checks also pass.
- The installed Linux wheel has SHA256
  `67d002fc907a4b9e991a9b28fef0c6daddf4daddfc1b2b3431833a0d362178f9`.
  A pristine interpreter containing only unstd passes the batch probe. A numeric
  binary frame produced on Linux decodes and re-encodes identically on macOS.

The Linux runtime is native arm64 inside a Docker Desktop Linux VM on a macOS
host, using container-owned overlayfs, CPython 3.14.8, and SQLite 3.46.1. macOS
uses native arm64 and CPython 3.14.9. This does not certify bare-metal Linux,
x86_64, or native Windows runtime behavior; Windows coverage here is simulated
behavior and targeted type checking. Broader shared-checkout validation remains
inconclusive, so these scoped passes are not a repository-wide clean claim.

Complete warm workflow speedups are 1.688× on macOS and 1.560× on Linux. The final
Linux cold result is neutral within host variation. The report retains the
earlier 10.6%/12.6% cold losses and the remaining narrow Linux callback cold loss.
Controlled replacement syscall counts are distinct from elapsed-time claims.

## Historical scale-upgrade validation

The scale-upgrade cache source SHA-256 was
`fad9f0ba829fc32a8d387d104c8ae806106e6e1a34b21f90a8b729bfee0e5956`;
the lock implementation is
`f459f15155e1010a62996d2bff438edf005841e9266f970a43780004136a9afa`.

The [batch](batches.json), [parallel-fill](parallel-fills.json), and
[actual Consumer loader](consumer-loader.json) reports retain these source identities
and complete measured outputs. They respectively check every batch result,
every producer interval, and complete source-backed loader output plus
content/version invalidation.

Validation at that capture:

- **129 cache tests** pass, covering scalar/batch contracts and real process
  coordination. The batch cases include an intervening committed write between
  query chunks and a later SQL failure that must roll back earlier batch rows.
- Strict mypy passed across all **195** product and benchmark source files at
  capture. Current scoped cache/driver types and lint pass. Subsequent concurrent
  math benchmark additions produce nine unrelated type errors in
  `bench/workloads/speed_cases_math.py` in a later 200-file full-tree check.
  Changed cache code, tests, guides and drivers pass Ruff formatting.
- Benchmark coverage passes **289 workloads across 20 packages**; its coverage
  gate passes 35 tests. The two new cache rows preserve all 287 existing rows,
  including the eight first-version cache ceilings, without relaxing them.
- Suite inventory and CI groups validate; `git diff --check` passes.
- An offline wheel build and [pristine installed probe](installed-scaled.json)
  pass with exactly one installed distribution (`unstd`) and no extras. The
  isolated interpreter round-trips 64 values, starts two independent producers
  before releasing either, writes an unrelated value while both run, and reuses
  a completed result in another process without a producer call. Installed
  source hashes match the current cache and lock sources above.
- A separate 12-thread stress experiment completed 480 batch/read/fill/delete
  sequences without errors or remaining owned lock descriptors.

Runtime tests and performance captures ran on macOS arm64. The Windows lock
adapter has simulated behavioral tests and Windows-targeted type checks; native
Windows runtime validation remains for that platform's CI.

The broad command at that capture
`uv run --no-sync pytest tests/suites/product/cache tests/suites/assurance/gates -q --tb=short`
finished with **3,333 passed, 6 failed, 6 skipped, 2 deselected**. Five failures
are the existing clone-profile/native-route failures reproduced on the baseline
below. One additional unrelated JSONX profile gate expects exactly nine public
cases, while the concurrent `jsonx.iterload` addition supplies a tenth. This
cache change does not alter that profile or the serialization operation registry.
The old filesystem signature failures in the first run below have since been
resolved by separate work in the shared checkout.

A later shared-checkout rename changed the source-backed driver to
`bench.profiles.consumer_cache` and its receipts to `consumer-loader.*`.
The actual-loader proof was rerun after that rename; its driver hash and current
cache hashes match the new receipt. That capture observed 957.9 ms cold and
243.8 ms warm (3.93×), with the same exact-output and invalidation checks.

## Historical first implementation validation

The frozen first cache source SHA-256 is
`de9162d4565f51fffd99115af8981d211ef099a8d2b4d449f4df37e94f213428`.
Both application and operation measurements retain this source identity.

### Passing checks

- 84 package-owned unit and integration tests, including a deterministic
  simultaneous-miss recheck, warm readers during a fill, killed-producer
  recovery, thread/fork reuse, no-extras fallback, expired-row eviction, and
  large-capacity boundaries.
- 217 combined cache, benchmark-coverage, and package-description checks.
- Strict mypy over all 186 product and benchmark source files.
- Ruff lint and formatting for changed code and cache guides; `git diff --check`.
- Full benchmark correctness/coverage: 278 workloads across 19 packages.
- Suite inventory and CI-group validation.
- Consumer consumer source hashes/symbol locations and proof source hashes.

```console
uv run --no-sync pytest tests/suites/product/cache tests/suites/assurance/gates/bench/test_speed_coverage.py tests/suites/assurance/gates/packaging/test_package.py -q
uv run --no-sync mypy src/unstd bench
uv run --no-sync python -m bench.speeds check
uv run --no-sync python -m tests.tooling check
uv run --no-sync python -m tests.tooling.ci check
```

### Installed wheel

An offline wheel build succeeded. The wheel was installed with `--no-deps` into
an unseeded virtual environment containing exactly one distribution, `unstd`.
Two fresh isolated Python processes, working outside the checkout, opened the
same database. They returned the expected identical JSON result and recorded
exactly one producer invocation across both processes. The installed cache's
source hash matches the source above. See [`installed.json`](installed.json).

### Existing gate failures

The broader command below completed with **3,197 passed, 6 skipped, 2 deselected,
and 7 failed**:

```console
uv run --no-sync pytest tests/suites/assurance/gates -q
```

All seven failures also reproduce on pristine baseline commit
`b3c5c57bf5fd445346c3cf54234da8995c366b9d`. The comparison used `git archive HEAD`
in a disposable snapshot, the same nine native binaries, baseline source/tests
first on the import path, and a fresh pytest process. The snapshot contains no
cache package; `unstd.cache` was never imported. The implicated source, tests,
profilers, and existing filesystem API snapshot match that commit.

- Two `test_clone_ids_profile.py` checks report the existing unprofiled
  `structural._model_private` helper.
- Three `test_native_profile.py` checks fail existing clone cycle-route
  expectations or the one-argument monkeypatch versus three-argument native
  call contract.
- Two `test_public_api.py` checks encounter the old `fs.ensure_dir` snapshot,
  which omits the existing `check_first=True` keyword parameter.

These failures are recorded without changing unrelated contracts or weakening
their checks. The new package's constructor snapshot and package-description
checks pass.
