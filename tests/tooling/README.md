# Test tooling

`python -m tests.tooling list|check|run <suite>` operates the suite inventory.
The inventory describes implemented versus planned ownership; it does not
declare all planned tests implemented or create required branch-protection
statuses. See [the suite guide](../README.md) for commands and evidence policy.

[`fuzz/`](fuzz/README.md) contains executable parser/token targets, authenticated
seed corpora, deterministic replay and mutation, and bounded Atheris campaigns.
`mutation/` reserves future mutation-score campaigns. `bench/` points to the existing benchmark instrument.
Pytest discovers only `tests/suites`; tests of these tools belong there too.
