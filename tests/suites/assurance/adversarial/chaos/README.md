# Chaos and recovery

Status: **active**. Recorded mixed filesystem/process histories, real writer
interruption, stream faults and clock/entropy outages have bounded experiments,
independent expectations, positive controls and recovery assertions.

See the [campaign runbook](../../../../tooling/chaos/README.md) for seeds,
replay, budgets, backend admission, process ownership, CI and evidence limits.

Run `uv run --no-sync python -m tests.tooling.chaos run --seed 173`, or
select this lane directly with
`uv run --no-sync python -m tests.tooling run chaos -- --chaos-seed 173 -q`.
