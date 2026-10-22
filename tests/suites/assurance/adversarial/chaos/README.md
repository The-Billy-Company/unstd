# Chaos and recovery

Status: **active**. Recorded mixed filesystem/process histories, real writer
interruption, stream faults and clock/entropy outages have bounded experiments,
independent expectations, positive controls and recovery assertions.

Strict durability histories cover preflight refusal, post-commit sync failure
and cancellation, combined sync/close failures, exact committed bytes, descriptor
ownership and a successful strict recovery after each fault.

See the [campaign runbook](../../../../tooling/chaos/README.md) for seeds,
replay, budgets, backend admission, process ownership, CI and evidence limits.

Run `uv run --no-sync python -m tests.tooling.chaos run --seed 173`, or
select this lane directly with
`uv run --no-sync python -m tests.tooling run chaos -- --chaos-seed 173 -q`.
