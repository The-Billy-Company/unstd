# unstd test suite

Tests follow [Billy's Python suite](../../billy/services/ai/tests/README.md):
**owner first, evidence kind second**, with support and standalone tools kept
separate from executable suites.

```text
tests/
├── conftest.py                 shared profiles, collection policy, markers
├── suites/
│   ├── product/<module>/       mirrors src/unstd/<module>
│   │   ├── unit/              existing API contracts
│   │   ├── parity/            dedicated differential tests
│   │   ├── properties/        dedicated Hypothesis laws
│   │   ├── security/          module-owned security contracts
│   │   └── regression/        minimized confirmed fuzz-discovered defects
│   └── assurance/             organized by cross-cutting risk
│       ├── adversarial/       chaos, concurrency, resources, corpus replay
│       ├── evals/performance/ dedicated performance evidence
│       ├── gates/             package and test-instrument correctness
│       ├── planes/            integrations and installed artifacts
│       └── security/          pentest and structural canaries
├── support/                   anchors, profiles, future fixtures/strategies
└── tooling/                   suite inventory/runner, fuzz campaigns, mutation plan
```

Only applicable Billy lanes are instantiated. A new module's properties stay
beside its API tests. A scenario spanning modules belongs under assurance.
Reusable fixtures and strategies go under support; assertions about a harness
belong under assurance/gates. Standalone campaigns go under tooling.

## What works now

The pre-existing tests have been relocated, preserving their assertions and
in-progress source changes. Dedicated JSON/Base64/regex parity and the dedicated
property modules have their own leaves. Other historical API files include
differential, generated, concurrent, and adverse tests; a `unit` directory is
their current owner, not a claim that every function uses only unit techniques.
Generated tests in mixed modules also receive the `properties` marker.

The [P0 boundary suites](P0.md) now implement installed packaging, real TLS peers,
filesystem/process failure handling, and parser/token security. TLS policy and
peer tests run without BLAKE3. Security expectations include independent MAC
envelopes, successful peer controls, observed native regex execution, and
bounded hostile parser workers. Remaining planned lanes cannot report a pass.

The [fuzz suites](tooling/fuzz/README.md) now provide eleven parser/token
targets, reviewed authenticated corpora, ordinary finite replay in nineteen
installed/stdlib combinations, reproducible byte mutation and genuine bounded
Atheris/libFuzzer campaigns. Their harness gates prove incorrect results,
unexpected exceptions, crashes, hangs and resource failures cannot become a
passing run. Python coverage instrumentation does not establish coverage inside
compiled dependencies.

## Run and inspect

Use the existing environment with `--no-sync` to retain the backend combination
being tested. Use `uv sync --locked --all-extras --group dev` to prepare a full
development environment, or omit `--all-extras` for the fallback leg.

```console
uv run --no-sync python -m tests.tooling list
uv run --no-sync python -m tests.tooling check
uv run --no-sync pytest -q
uv run --no-sync pytest --require-extras -q
uv run --no-sync python -m tests.tooling run parity -- -q
uv run --no-sync pytest -q -m properties
HYPOTHESIS_PROFILE=ci uv run --no-sync pytest -q -m properties
HYPOTHESIS_PROFILE=campaign uv run --no-sync pytest -q -m properties
uv run --no-sync python -m tests.tooling run fuzz-replay -- -q
uv run --no-sync python -m tests.tooling.fuzz check
uv run --no-sync python -m tests.tooling.fuzz replay --target all --backend stdlib
uv run --no-sync python -m tests.tooling.fuzz campaign --target json --backend installed --engine deterministic --runs 1000 --seed 17
```

The default run discovers only `tests/suites`, with strict configuration,
registered markers, and importlib collection. It excludes `perf` and `chaos`.
Three existing wall-clock benchmark calibration checks carry `perf`; run them
explicitly with `uv run --no-sync pytest -q -m perf` on a quiet machine. The
cross-module product performance lane remains planned. Other excluded work
requires explicit selection when implemented. A runner invocation selects
the requested lane's marker and excludes `perf`/`chaos` unless one of those is
the requested kind; extra pytest arguments may override selection.
The `properties` runner lane selects the dedicated property directories;
`pytest -m properties` includes generated cases in historical mixed files too.

[inventory.toml](tooling/inventory.toml) is the suite ownership source. `check`
rejects unowned test files, overlapping paths, missing active suites, empty
active suites, invalid status/markers, and paths escaping the suite tree.
Collection also requires exactly one active owner per test. `run` refuses
planned lanes. A lane becomes active only after its tests can falsify the
claimed contract; empty directories and skipped placeholders are not coverage.
Inventory validity does not imply behavioral completion or a new required CI
status. CI retains the behavioral matrix, validates inventory, and requires all
declared extras on its all-extra and floor legs. `--require-extras` makes missing
capabilities fatal and admits the isolated each-extra installed consumers;
optional local/fallback runs retain explicit capability skips.
The `fuzz-replay` lane is active and participates in ordinary discovery;
standalone mutation requires the explicit campaign command. Installed replay
uses the same optional/required capability policy, while its stdlib workers
block optional imports before target loading.

## Shared evidence policy

- Use stdlib behavior, standards, published vectors, independent reference
  models, or reviewed frozen cases for expected results. A round trip alone
  cannot establish fidelity when both halves share a bug.
- Pair security rejection probes with successful controls. Put hostile regex,
  native parser, process, and resource cases in bounded killable workers.
- Keep property depth in [shared profiles](support/hypothesis/profiles.py).
  Confirmed failures become named regression cases or explicit examples; the
  local Hypothesis database is a cache, not the permanent regression record.
- Run fault injection through real public seams. Fail missing prerequisites in
  required CI lanes; label optional environments explicitly rather than silently
  substituting weaker evidence.
- Keep timing and sustained mutation outside ordinary correctness execution.
  Preserve seeds, inputs, backend/interpreter versions, and failure artifacts.

[COVERAGE.md](COVERAGE.md) defines the actual module obligations, priorities,
platform matrix, and remaining gaps. The [fuzz runbook](tooling/fuzz/README.md)
documents executable replay, campaign budgets, Atheris setup, artifacts and
exact reproduction. [Mutation](tooling/mutation/README.md) defines the separate
planned source-mutation lifecycle.
