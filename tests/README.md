# unstd test suite

Tests are organized by owner, then evidence kind. [Product suites](suites/product/README.md)
mirror `src/unstd/<module>`; [assurance suites](suites/assurance/README.md) own
cross-module risks and test-tool correctness. Reusable workers and fixtures live
in [support](support/README.md); standalone runners live in [tooling](tooling/README.md).
`conftest.py` applies generation profiles, inventory ownership, and backend policy.

The [inventory](tooling/inventory.toml) defines every lane's paths, marker, and
active/planned status. Every test file needs exactly one active owner. New tests
belong beside the contract they exercise; shared support has no standalone
product assertions. The [coverage plan](COVERAGE.md) records obligations and gaps;
[P0.md](P0.md) details boundary coverage.

## Run and inspect

From the repository root, prepare a full environment with
`uv sync --locked --all-extras --group dev`; omit `--all-extras` for the fallback
configuration. `--no-sync` preserves the backend combination already installed.
`--require-extras` makes every declared extra mandatory, including isolated
per-extra consumers in the packaging lane. Optional local/base runs retain
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
| `gates` | [Tests of repository and quality instruments](suites/assurance/gates/README.md), including dependency and release policy |

Run opt-in correctness lanes explicitly:

```console
uv run --no-sync python -m tests.tooling run chaos -- --chaos-seed 173 -q
uv run --no-sync python -m tests.tooling run concurrency -- --concurrency-seed 173 -q
uv run --no-sync python -m tests.tooling run resources -- -q
```

The [fuzz runbook](tooling/fuzz/README.md) covers deterministic byte mutation,
bounded Atheris/libFuzzer campaigns, artifacts, and reproduction. Standalone
chaos/concurrency campaigns use their linked runbooks. The `performance` and
`system-integration` lanes remain planned; the runner refuses to run them. Three
existing benchmark timer calibration checks use `perf` and can be selected with
`uv run --no-sync pytest -q -m perf` on a quiet machine. Source
[mutation testing](tooling/mutation/README.md) also remains planned.

## CI and evidence policy

The main CI workflow runs four disjoint groups: API & regressions, Compatibility
& boundaries, Test tooling & corpus, and Packaging & consumers. Each runs on
Python 3.12, 3.13, and 3.14 with all extras, plus Python 3.13 dependency-floor and
base-install environments. Groups derive paths from the inventory; new active
default suites must join exactly one group.

```console
uv run --no-sync python -m tests.tooling.ci list
uv run --no-sync python -m tests.tooling.ci check
uv run --no-sync python -m tests.tooling.ci run contracts -- --require-extras -q
```

Each group writes `.local/ci/<group>.xml` and a job summary; CI retains reports
for 14 days. Separate required jobs cover quality checks, dependency policy,
native safety, and Linux/macOS resource bounds. The aggregate `CI / Required groups`
requires its dependencies to succeed; releases invoke the same CI workflow.
Fuzz, chaos, and concurrency workflows run explicit bounded campaigns and retain
failure artifacts.

Use stdlib behavior, standards, published vectors, independent models, or reviewed
frozen cases as expectations. A round trip alone cannot prove fidelity. Pair
security rejections with successful controls, and place hostile native/parser,
process, and resource cases in bounded killable workers. Keep timing and sustained
mutation outside ordinary correctness execution. Preserve confirmed failures as
named owning regressions or explicit examples; a local Hypothesis database is a
cache, not a permanent regression record.
