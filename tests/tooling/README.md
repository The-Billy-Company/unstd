# Test tooling

Each tool owns a package; the root contains only this guide and the shared
`python -m tests.tooling` entry point.

| Package | Responsibility |
| --- | --- |
| [`suites/`](suites/) | Suite inventory, ownership validation, and pytest selection |
| [`ci/`](ci/) | CI groups, JUnit summaries, and shared campaign reporting |
| [`release/`](release/) | Archive verification and authenticated release handoff |
| [`supply_chain/`](supply_chain/) | Dependency trust, lockfile inventory, and audit evidence |
| [`static_analysis/`](static_analysis/) | Bandit and SARIF evidence validation |
| [`fuzz/`](fuzz/README.md) | Parser/token targets, corpora, replay, and campaigns |
| [`chaos/`](chaos/README.md) | Recorded fault/recovery campaigns |
| [`concurrency/`](concurrency/README.md) | Recorded thread/process campaigns |
| [`bench/`](bench/README.md) | Existing benchmark instrument |
| [`mutation/`](mutation/README.md) | Planned mutation-score campaigns |

`python -m tests.tooling list|check|run <suite>` operates the suite inventory.
The [inventory](suites/inventory.toml) describes implemented versus planned
ownership; it does not declare all planned tests implemented or create required
branch-protection statuses. See [the suite guide](../README.md) for commands and
evidence policy.

`python -m tests.tooling.ci list|check|run <group>` composes those owned suites
into four complete, disjoint CI groups: `api`, `contracts`, `tooling`, and
`packaging`. Each run retains a JUnit report under `.local/ci/` and appends a
result summary when `GITHUB_STEP_SUMMARY` is set. The group validator rejects
new active default suites until their CI group is declared; planned suites and
explicit chaos/concurrency/resource campaigns remain separate.

[`supply_chain/policy.py`](supply_chain/policy.py) checks dependency trust offline,
exports a deterministic CycloneDX inventory of every lockfile platform branch,
and rejects incomplete or non-clean `uv audit` reports.
[`release/verify.py`](release/verify.py) verifies archives without importing their
contents and authenticates the exact files handed to PyPI. See
[supply chain testing](../suites/assurance/gates/supply_chain/README.md) for local
commands, CI ownership, release handoff and limits.

[`static_analysis/reports.py`](static_analysis/reports.py) rejects non-clean,
missing, malformed, and unsuccessful Bandit/SARIF evidence. The [SAST policy](../../SECURITY.md)
explains scanner scope and local commands; its [gate tests](../suites/assurance/gates/static_analysis/README.md)
exercise real insecure/duplicate seeds and failures as well as hostile reports.

[`fuzz/`](fuzz/README.md) contains executable parser/token targets, authenticated
seed corpora, deterministic replay and mutation, and bounded Atheris campaigns.
[`chaos/`](chaos/README.md) runs recorded fault/recovery schedules in bounded
installed/stdlib workers, with exact replay and retained observation journals.
[`concurrency/`](concurrency/README.md) runs bounded thread/process histories for
atomic files, UUID state and shared caches/codecs, with recorded schedules and
independent ownership and ordering checks.
The finite [resource lane](../suites/assurance/adversarial/resources/README.md)
uses `python -m tests.tooling run resources`; its worker support lives under
`tests/support`, without a separate campaign CLI.
`mutation/` reserves future mutation-score campaigns. `bench/` points to the existing benchmark instrument.
Pytest discovers only `tests/suites`; tests of these tools belong there too.
