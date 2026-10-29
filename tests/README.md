---
doc_radar:
  sentinels:
    - file: ../.github/workflows/ci.yml
      contains: ['dependencies: "--group release --group build"']
    - file: suites/assurance/gates/release/test_wheel_runtimes.py
      contains: ['from cibuildwheel.options import CommandLineArguments, compute_options', 'from setuptools._distutils.ccompiler import new_compiler']
---

# unstd test suite

Tests are organized by owner, then evidence kind. [Product suites](suites/product/README.md)
mirror `src/unstd/<module>`; [assurance suites](suites/assurance/README.md) own
cross-module risks and test-tool correctness. Reusable workers and fixtures live
in [support](support/README.md); standalone runners live in [tooling](tooling/README.md).
`conftest.py` applies generation profiles, inventory ownership, and backend policy.

The [inventory](tooling/suites/inventory.toml) defines every lane's paths, marker, and
active/planned status. Every test file needs exactly one active owner. New tests
belong beside the contract they exercise; shared support has no standalone
product assertions. The [coverage plan](COVERAGE.md) records obligations and gaps;
[P0.md](P0.md) details boundary coverage.

## Run and inspect

The [supply chain gate](suites/assurance/gates/supply_chain/README.md) checks
dependency sources and hashes, audits all locked environments, and retains a
CycloneDX inventory. Its tamper controls run in ordinary tooling coverage and a
small dedicated CI job. Release builds authenticate their build/test closures,
then validate the exact archives and checksums handed to the PyPI publisher.

From the repository root, prepare a full environment with
`uv sync --locked --all-extras --group dev --group release --group build --reinstall-package unstd`; omit `--all-extras` for the fallback
configuration. The development interpreter is pinned to Python 3.15.2 in
`.python-version`.
Reinstall after native source changes so local checks exercise the rebuilt C
extensions, as CI does. `--no-sync` preserves the backend combination and compiled
extensions already installed.
The tooling owner installs the release and build groups in all six CI environments;
its builder and compiler checks require those tools.
`--require-extras` makes every declared extra mandatory, including isolated
per-extra consumers in the packaging lane. A partial install (for example
`uv sync --locked --extra text --group dev --group release --group build`) instead passes
`--require-extra text`, repeatable, so only its own extras are mandatory while
every other backend stays genuinely absent. Optional local/base runs retain
explicit capability skips.

```console
uv run --no-sync python -m tests.tooling list
uv run --no-sync python -m tests.tooling check
uv run --no-sync pytest -q
uv run --no-sync pytest --require-extras -q
uv run --no-sync python -m tests.tooling run parity -- -q
uv run --no-sync pytest -q -m properties
HYPOTHESIS_PROFILE=ci uv run --no-sync pytest -q -m properties
uv run --no-sync pytest --require-extras -q -m 'security or pentest or security_structure or integration'
```

Default pytest discovers `tests/suites` with strict configuration and importlib
collection. It excludes `perf`, `chaos`, `concurrency`, and `resources`. Select an
active lane with `python -m tests.tooling run LANE -- PYTEST_ARGS`; the runner
applies its marker and paths. Explicit pytest arguments may override its marker
selection. The `properties` runner selects dedicated property directories;
`pytest -m properties` also includes generated tests retained in mixed API files.
The [shared Hypothesis profiles](support/hypothesis/README.md) control depth.

## Cross-cutting lanes

| Lane | Current evidence and runbook |
| --- | --- |
| `packaging` | [Offline wheel/sdist builds](suites/assurance/planes/packaging/README.md), archive integrity, pristine base/per-extra consumers, and downstream typing |
| `pentest` | [Real loopback TLS peers](suites/assurance/security/pentest/README.md) proving trust, identity, client authentication, and protocol policy |
| `security-structure` | [Live MAC and secure-random primitive canaries](suites/assurance/security/structure/README.md) |
| `fuzz-replay` | [Authenticated committed corpora](suites/assurance/adversarial/fuzz_replay/README.md) in bounded installed/stdlib workers; included in ordinary pytest |
| `chaos` | [Recorded fault schedules and recovery](tooling/chaos/README.md) across files/processes, streams, clocks, and entropy |
| `concurrency` | [Filesystem, UUID, and shared-state histories](tooling/concurrency/README.md) with controlled overlap and independent models |
| `resources` | [Streaming memory, cleanup, cache lifetime, and JSON depth bounds](suites/assurance/adversarial/resources/README.md) in fresh workers |
| `performance` | [Per-function latency ceilings](suites/assurance/evals/performance/README.md) with audited workloads and a frozen baseline for the selected worker |
| `gates` | [Tests of repository and quality instruments](suites/assurance/gates/README.md), including dependency and release policy |

Run opt-in correctness lanes explicitly:

```console
uv run --no-sync python -m tests.tooling run chaos -- --chaos-seed 173 -q
uv run --no-sync python -m tests.tooling run concurrency -- --concurrency-seed 173 -q
uv run --no-sync python -m tests.tooling run resources -- -q
```

The [fuzz runbook](tooling/fuzz/README.md) covers deterministic byte mutation,
bounded Atheris/libFuzzer campaigns, artifacts, and reproduction. Standalone
chaos/concurrency campaigns use their linked runbooks. The `performance` lane is
active and opt-in: run `uv run --no-sync python -m tests.tooling run performance -- -q`
on the baseline's recorded environment, or set `UNSTD_SPEED_BASELINE` to an
unchanged baseline captured on the comparison worker. `pytest -m perf` selects
this gate and the existing benchmark timer calibration checks. The
`system-integration` lane remains planned and cannot run. Source
[mutation testing](tooling/mutation/README.md) also remains planned.

## CI and evidence policy

The main CI workflow runs four disjoint groups: API & regressions, Compatibility
& boundaries, Test tooling & corpus, and Packaging & consumers. Each runs on
Python 3.12, 3.13, 3.14, and 3.15 with all extras, plus Python 3.15 dependency-floor and
base-install environments. Groups derive paths from the inventory; new active
default suites must join exactly one group.

Dependency-floor jobs allow 25 minutes for cold native dependency builds and
tests; the other grouped environments allow 15 minutes. Per-test limits and
Hypothesis generation profiles remain the same in every environment.

Python 3.15 also runs the macOS/Windows public contracts, partial-extra installs,
resource bounds, sanitizer checks, Rust experiment, and quality/release tooling.
Sanitizers also retain Python 3.14 coverage for its guarded native internals.
Fuzz replay and deterministic mutation cover 3.15 alongside the Python 3.12
Atheris environment; chaos and concurrency campaigns run on 3.15.

```console
uv run --no-sync python -m tests.tooling.ci list
uv run --no-sync python -m tests.tooling.ci check
uv run --no-sync python -m tests.tooling.ci run contracts -- --require-extras -q
uv run --no-sync python -m tests.tooling.ci run --shard 1/3 contracts -- --require-extras -q
```

Each group writes `.local/ci/<group>.xml` and a job summary; CI retains reports
for 14 days. Separate required jobs cover quality checks, dependency policy,
native safety, and Linux/macOS resource bounds. The aggregate `CI / Required groups`
requires its dependencies to succeed; releases invoke the same CI workflow.
Fuzz, chaos, and concurrency workflows run explicit bounded campaigns and retain
failure artifacts.

The contracts group and both sanitizer variants run three deterministic shards.
Each test belongs to exactly one shard; generation budgets and assertions remain
unchanged. Sharded reports use `<group>-<index>-of-<total>.xml`, keeping every
partition's evidence distinct.

Use stdlib behavior, standards, published vectors, independent models, or reviewed
frozen cases as expectations. A round trip alone cannot prove fidelity. Pair
security rejections with successful controls, and place hostile native/parser,
process, and resource cases in bounded killable workers. Keep timing and sustained
mutation outside ordinary correctness execution. Preserve confirmed failures as
named owning regressions or explicit examples; a local Hypothesis database is a
cache, not a permanent regression record.
