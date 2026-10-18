# Concurrency histories

Status: **active**. Three executable groups cover atomic filesystem visibility
and ownership, UUID contention and fork safety, and shared clone/regex/codec/YAML
state plus immutable-map branches. Independent models and deliberately corrupted
histories test the assertions themselves. Every scenario has a bounded worker,
controlled overlap and a recorded journal; there are no placeholder tests.

Run `uv run --no-sync python -m tests.tooling.concurrency run --seed 173` for
the supervised installed-backend campaign, or use `--backend stdlib` for import
blocking and explicit optional-only skips. Direct selection is
`uv run --no-sync python -m tests.tooling run concurrency -- --concurrency-seed 173 -q`.
The lane is excluded from default pytest. Confirmed defects also have ordinary
regressions under their owning product modules.

The [runbook](../../../../tooling/concurrency/README.md) maps scenarios, replay,
limits, backend policy, evidence and CI. Tests of the runner belong under
`assurance/gates/concurrency`; reusable worker mechanics belong under support.
