# Test tooling

`python -m tests.tooling list|check|run <suite>` operates the suite inventory.
The inventory describes implemented versus planned ownership; it does not
declare all planned tests implemented or create required branch-protection
statuses. See [the suite guide](../README.md) for commands and evidence policy.

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
